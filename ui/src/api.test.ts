import {afterEach,expect,it,vi} from 'vitest';
import {api,setCsrf,uploadFile} from './api';
afterEach(()=>vi.unstubAllGlobals());
it('sends the session CSRF token on writes without converting them to GET',async()=>{
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({ok:true,data:{operationId:'original-id'}}),{status:200}));vi.stubGlobal('fetch',fetch);setCsrf('session-csrf');
 expect(await api('prepare',{kind:'instance.file.write'})).toEqual({operationId:'original-id'});
 const request=fetch.mock.calls[0][1];expect(request.method).toBe('POST');expect(request.headers['X-CSRF-Token']).toBe('session-csrf');expect(request.credentials).toBe('same-origin');
});
it('never treats a rejected receipt as success or retries a write',async()=>{
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({ok:false,message:'结果待核对'}),{status:503}));vi.stubGlobal('fetch',fetch);
 await expect(api('confirm',{operationId:'original-id'})).rejects.toThrow('结果待核对');expect(fetch).toHaveBeenCalledTimes(1);
});
it('invalidates the browser session after an HTTP 401',async()=>{
 const dispatchEvent=vi.fn();vi.stubGlobal('window',{dispatchEvent});vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({ok:false,message:'请重新登录'}),{status:401})));
 await expect(api('session')).rejects.toThrow('请重新登录');expect(dispatchEvent.mock.calls[0][0].type).toBe('breakline:session-expired');
});
it('streams the original upload body with CSRF and keeps confirmation separate',async()=>{
 const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({ok:true,data:{operationId:'upload-preview'}}),{status:200}));vi.stubGlobal('fetch',fetch);setCsrf('upload-csrf');const file=new File(['binary upload'],'fixture.jar');
 expect(await uploadFile('hub','mods/fixture.jar',file)).toEqual({operationId:'upload-preview'});const request=fetch.mock.calls[0][1];expect(request.body).toBe(file);expect(request.headers['Content-Type']).toBe('application/octet-stream');expect(request.headers['X-CSRF-Token']).toBe('upload-csrf');expect(fetch).toHaveBeenCalledTimes(1);
});
