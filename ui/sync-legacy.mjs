import fs from 'node:fs';
import postcss from 'postcss';
const html=fs.readFileSync('../web/index.html','utf8').split('<body>')[1].split('</body>')[0];
fs.writeFileSync('src/legacy.html',html);
const root=postcss.parse(fs.readFileSync('../web/panel.css','utf8'));
root.walkRules(rule=>{if(rule.parent.type==='atrule' && rule.parent.name.includes('keyframes'))return;rule.selectors=rule.selectors.map(s=>s.includes(':root')?s.replaceAll(':root','#legacy-host'):'#legacy-host '+s);});
fs.writeFileSync('src/legacy.css',root.toString());
