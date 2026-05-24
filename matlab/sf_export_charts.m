function pngPaths = sf_export_charts(modelPath, outputDir)
% Export all Stateflow charts in a Simulink model to PNG files for review.
%
%   modelPath  - path to .slx file, or model name if already loaded
%   outputDir  - folder to write PNGs (default: <modelDir>/<modelName>_charts/)
%   pngPaths   - cell array of written PNG file paths

[modelDir, modelName, ~] = fileparts(char(modelPath));

if ~bdIsLoaded(modelName)
    load_system(modelPath);
end

% If only a name was given, find dir from MATLAB path
if isempty(modelDir)
    modelDir = fileparts(which([modelName '.slx']));
    if isempty(modelDir), modelDir = pwd; end
end

if nargin < 2 || isempty(outputDir)
    outputDir = fullfile(modelDir, [modelName '_charts']);
end
if ~exist(outputDir, 'dir'), mkdir(outputDir); end

rt = sfroot;
machine = rt.find('-isa', 'Stateflow.Machine', 'Name', modelName);
if isempty(machine)
    fprintf('No Stateflow machine found in model: %s\n', modelName);
    pngPaths = {};
    return;
end

% Top-level charts
charts = machine.find('-isa', 'Stateflow.Chart');
pngPaths = {};
for i = 1:numel(charts)
    safeName = regexprep(charts(i).Path, '[/\\:*?"<>| ]', '_');
    outFile  = fullfile(outputDir, [safeName '.png']);
    sfprint(charts(i), 'png', outFile);
    pngPaths{end+1} = outFile; %#ok<AGROW>
    fprintf('  [chart]    -> %s\n', outFile);
end

% Subchart states (IsSubchart=true — collapsed in parent view, need separate export)
subcharts = machine.find('-isa', 'Stateflow.State', 'IsSubchart', true);
for i = 1:numel(subcharts)
    fullPath = [subcharts(i).Path '/' subcharts(i).Name];
    safeName = regexprep(fullPath, '[/\\:*?"<>| ]', '_');
    outFile  = fullfile(outputDir, [safeName '.png']);
    sfprint(subcharts(i), 'png', outFile);
    pngPaths{end+1} = outFile; %#ok<AGROW>
    fprintf('  [subchart] -> %s\n', outFile);
end

fprintf('Exported %d image(s) to: %s\n', numel(pngPaths), outputDir);
end
