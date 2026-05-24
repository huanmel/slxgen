const ELK = require('elkjs/lib/elk.bundled.js');
const elk = new ELK();

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', () => {
    const graph = JSON.parse(input);
    elk.layout(graph)
       .then(result => { process.stdout.write(JSON.stringify(result)); })
       .catch(err  => { process.stderr.write(String(err)); process.exit(1); });
});
