function issues = sfLintChart(ch, scopeObj)
% sfLintChart  Structural lint checker for a Stateflow chart.
%
%   ISSUES = sfLintChart(CH)
%   ISSUES = sfLintChart(CH, SCOPEOBJ)
%
%   CH        - Stateflow.Chart obtained via:
%                 find(sfroot, '-isa', 'Stateflow.Chart', 'Path', path)
%   SCOPEOBJ  - (optional) Stateflow.State to restrict checks to one
%               superstate/subchart subtree. When omitted the whole chart
%               is checked.
%
%   Returns a 0-or-more element struct array.  Each element has:
%     handle   Stateflow object carrying the issue (Chart or State).
%     name     Short category tag (char).  One of:
%                NoDefaultTransition
%                MultipleDefaultTrans
%                UnreachableState
%                MissingDestination
%                DuplicateExecutionOrder
%                DuplicateStateName
%     details  Human-readable sentence describing the specific violation.
%
% Implementation note:
%   Stateflow stores ALL transitions at chart level when states are
%   non-subchart (only subchart transitions are scoped to their subchart).
%   Therefore this checker determines container membership from the
%   source/destination state paths rather than from Transition.Path.

narginchk(1, 2);
if nargin < 2
    scopeObj = ch;
end

issues = struct('handle', {}, 'name', {}, 'details', {});

%% 1. Collect all Stateflow objects within the scope -----------------------
allStates = find(ch, '-isa', 'Stateflow.State');
allTrans  = find(ch, '-isa', 'Stateflow.Transition');

if ~isa(scopeObj, 'Stateflow.Chart')
    scopeFull = containerFullPath(scopeObj);
    prefix    = [scopeFull '/'];
    pLen      = length(prefix);

    keep = strcmp({allStates.Path}, scopeFull) | ...
           strncmp({allStates.Path}, prefix, pLen);
    allStates = allStates(keep);

    keep = false(1, numel(allTrans));
    for ti = 1:numel(allTrans)
        t = allTrans(ti);
        srcIn = ~isempty(t.Source) && ...
                (strcmp(t.Source.Path, scopeFull) || strncmp(t.Source.Path, prefix, pLen));
        dstIn = ~isempty(t.Destination) && ...
                (strcmp(t.Destination.Path, scopeFull) || strncmp(t.Destination.Path, prefix, pLen));
        keep(ti) = srcIn || dstIn;
    end
    allTrans = allTrans(keep);
end

%% 2. Build list of containers --------------------------------------------
containers = buildContainers(scopeObj, allStates);

%% 3. Per-container checks ------------------------------------------------
for ci = 1:numel(containers)
    c     = containers{ci};
    cFull = containerFullPath(c);   % full path to the container itself

    % Direct children of this container (states whose parent-path == cFull)
    childStates = allStates(strcmp({allStates.Path}, cFull));

    if isempty(childStates)
        continue   % pure leaf — nothing to validate here
    end

    % AND (parallel) containers — skip entry/reachability checks
    isAndContainer = false;
    if isa(c, 'Stateflow.State')
        isAndContainer = strcmp(c.Decomposition, 'PARALLEL_AND');
    end

    % Default transitions for this container:
    %   no source AND destination is a direct child (dest.Path == cFull).
    %   Uses destination path, not transition.Path, because Stateflow stores
    %   all non-subchart transitions at chart level.
    defMask = arrayfun(@(t) isempty(t.Source) && ...
                            ~isempty(t.Destination) && ...
                            strcmp(t.Destination.Path, cFull), allTrans);
    nDefault = sum(defMask);

    % Intra-container transitions: both source and destination are direct children
    innerMask = arrayfun(@(t) ~isempty(t.Source) && ~isempty(t.Destination) && ...
                               strcmp(t.Source.Path,      cFull) && ...
                               strcmp(t.Destination.Path, cFull), allTrans);
    innerTrans = allTrans(innerMask);

    % -- 3a. Default transition presence ----------------------------------
    if ~isAndContainer
        if nDefault == 0
            issues = [issues; mkIssue(c, 'NoDefaultTransition', ...
                sprintf('"%s" has %d child state(s) but no default transition.', ...
                        cFull, numel(childStates)))]; %#ok<AGROW>
        elseif nDefault > 1
            issues = [issues; mkIssue(c, 'MultipleDefaultTrans', ...
                sprintf('"%s" has %d default transitions; exactly 1 is required.', ...
                        cFull, nDefault))]; %#ok<AGROW>
        end
    end

    % -- 3b. Unreachable states -------------------------------------------
    % A direct child S is reachable when any transition has:
    %   (a) Destination.Path == cFull  (target IS the direct child), OR
    %   (b) Destination.Path starts with [cFull '/' S.Name]
    %       (target is a deep substate of S — entering S transitively).
    if ~isAndContainer
        reachableNames = {};
        deepPrefix = [cFull '/'];
        deepPLen   = length(deepPrefix);
        for ti = 1:numel(allTrans)
            t = allTrans(ti);
            if isempty(t.Destination), continue; end
            if strcmp(t.Destination.Path, cFull)
                % (a) direct child targeted — skip junctions (no Name property)
                if ~isa(t.Destination, 'Stateflow.Junction')
                    reachableNames{end+1} = t.Destination.Name; %#ok<AGROW>
                end
            elseif strncmp(t.Destination.Path, deepPrefix, deepPLen)
                % (b) deep substate: extract the direct-child name from the path
                remainder = t.Destination.Path(deepPLen+1:end);
                slashIdx  = strfind(remainder, '/');
                if isempty(slashIdx)
                    reachableNames{end+1} = remainder; %#ok<AGROW>
                else
                    reachableNames{end+1} = remainder(1:slashIdx(1)-1); %#ok<AGROW>
                end
            end
        end
        for si = 1:numel(childStates)
            s = childStates(si);
            if ~any(strcmp(reachableNames, s.Name))
                issues = [issues; mkIssue(s, 'UnreachableState', ...
                    sprintf('State "%s" in "%s" has no incoming transitions.', ...
                            s.Name, cFull))]; %#ok<AGROW>
            end
        end
    end

    % -- 3c. Duplicate execution orders per source ------------------------
    % Self-transitions (Source == Destination) are managed by Stateflow with an
    % independent execution counter and must not be mixed with regular outgoing
    % transitions when checking for duplicate orders.
    if ~isempty(innerTrans)
        for si = 1:numel(childStates)
            srcState = childStates(si);
            % Skip transitions involving junctions — they have no Name property.
            fromMask = arrayfun(@(t) ~isa(t.Source, 'Stateflow.Junction') && ...
                                     ~isa(t.Destination, 'Stateflow.Junction') && ...
                                     strcmp(t.Source.Name, srcState.Name) && ...
                                     ~strcmp(t.Destination.Name, t.Source.Name), innerTrans);
            fromSrc  = innerTrans(fromMask);
            if numel(fromSrc) >= 2
                orders = [fromSrc.ExecutionOrder];
                if numel(orders) ~= numel(unique(orders))
                    issues = [issues; mkIssue(srcState, 'DuplicateExecutionOrder', ...
                        sprintf('State "%s": outgoing transitions share ExecutionOrder value(s) [%s].', ...
                                srcState.Name, num2str(sort(orders))))]; %#ok<AGROW>
                end
            end
        end
    end

    % -- 3d. Duplicate sibling state names --------------------------------
    childNames = {childStates.Name};
    for uName = unique(childNames)
        idx = find(strcmp(childNames, uName{1}));
        if numel(idx) > 1
            issues = [issues; mkIssue(childStates(idx(1)), 'DuplicateStateName', ...
                sprintf('Name "%s" shared by %d siblings in "%s".', ...
                        uName{1}, numel(idx), cFull))]; %#ok<AGROW>
        end
    end
end

%% 4. Global: transitions with source but no destination ------------------
for ti = 1:numel(allTrans)
    t = allTrans(ti);
    if ~isempty(t.Source) && isempty(t.Destination)
        if isa(t.Source, 'Stateflow.Junction')
            srcLabel = '[connective junction]';
        else
            srcLabel = t.Source.Name;
        end
        issues = [issues; mkIssue(t, 'MissingDestination', ...
            sprintf('Transition from "%s" has no destination.', srcLabel))]; %#ok<AGROW>
    end
end

%% 5. Global: superfluous passthrough junctions ---------------------------
% A junction is superfluous when it has exactly 1 incoming and 1 outgoing
% transition that continue in the same direction and carry no conflicting
% labels — it can be replaced by a single direct transition.
allJunctions = find(ch, '-isa', 'Stateflow.Junction');
for ki = 1:numel(allJunctions)
    j    = allJunctions(ki);
    tIn  = sinkedTransitions(j);
    tOut = sourcedTransitions(j);
    if isscalar(tIn) && isscalar(tOut)
        theta    = abs(tIn.DestinationOClock - tOut.SourceOClock);
        samedir  = abs(theta - 6) < 1.5;
        noActBeforeCond = isempty(tIn.ConditionAction) || ...
            (isempty(tOut.Trigger) && isempty(tOut.Condition));
        noDoubleTrig = isempty(tIn.Trigger) || isempty(tOut.Trigger);
        if samedir && noActBeforeCond && noDoubleTrig
            issues(end+1) = mkIssue(j, 'SuperfluousJunction', ...
                sprintf('Junction %d in %s: 1-in/1-out passthrough — replace with a direct transition', ...
                    j.SSIdNumber, j.Path)); %#ok<AGROW>
        end
    end
end

end % sfLintChart

% =========================================================================
function fp = containerFullPath(c)
% Returns the full Stateflow path to the container itself.
%   Chart: its own .Path  (e.g. "Model/Chart")
%   State: [.Path '/' .Name]  (e.g. "Model/Chart/OFF")
if isa(c, 'Stateflow.Chart')
    fp = c.Path;
else
    fp = [c.Path '/' c.Name];
end
end

% =========================================================================
function containers = buildContainers(scopeObj, allStates)
% Returns a cell array of container objects (states that have direct children,
% plus the scope root).
%
% A state S is a container when any other state has .Path == S's full path
% (meaning those states are direct children of S).
containers = {scopeObj};
if isempty(allStates)
    return
end
allPaths = {allStates.Path};   % parent-paths of all states
for si = 1:numel(allStates)
    s     = allStates(si);
    sFull = [s.Path '/' s.Name];  % s's own full path
    if any(strcmp(allPaths, sFull))
        if isa(scopeObj, 'Stateflow.State') && s.Id == scopeObj.Id
            continue
        end
        containers{end+1} = s; %#ok<AGROW>
    end
end
end

% =========================================================================
function iss = mkIssue(handle, name, details)
iss = struct('handle', handle, 'name', name, 'details', details);
end
