import {describe,it,expect} from 'vitest';
import {complete,type Guide} from './completion';
const guides:Guide[]=[{en:'ban',zh:'封禁玩家',description:'封禁登录',template:'ban {角色名} {原因}',parameters:[{name:'角色名',type:'player'},{name:'原因',type:'text'}]},{en:'forge tps',zh:'查询 TPS',description:'查看耗时',template:'forge tps',parameters:[]}];
describe('command completion',()=>{
 it('finds commands by Chinese, slash prefix and multiword roots',()=>{expect(complete('封禁',guides)[0].value).toBe('ban ');expect(complete('/forge',guides)[0].value).toBe('forge tps');});
 it('completes exact player argument without replacing the command',()=>{expect(complete('ban Al',guides,['Alice','Bob'])[0].value).toBe('ban Alice ');expect(complete('ban Alice reason',guides,['Alice'])).toEqual([]);});
 it('never invents a command or a player',()=>{expect(complete('unknown',guides)).toEqual([]);expect(complete('ban Z',guides,['Alice'])).toEqual([]);});
});
