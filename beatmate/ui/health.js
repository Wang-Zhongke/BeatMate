import { $, el } from './common.js';

export function setupHealth({api,getConfig}) {
    let running=false;
    async function refresh(){
        const target=$('health-checks');
        if(getConfig()?.ui_version<4 || !getConfig()?.ui_version){
            target.replaceChildren(el('p','请停止原服务，运行 ./start.command，再刷新页面。','error'));
            $('network-check').disabled=true;
            return;
        }
        try {
            const report=await api('/audio/health');
            target.replaceChildren(...report.checks.map(check=>{
                const row=el('div',null,'health-row');
                row.append(el('strong',check.name),el('span',check.detail,check.status==='ok'?'help':'error'));
                return row;
            }));
            $('runtime-info').textContent=`音乐库：${report.audio_dir}\n配置：${report.config_file}\n运行版本：${report.revision}\n${report.proxy}`;
            if(report.environment_overrides.length) $('runtime-info').textContent+='\n环境变量优先项：'+report.environment_overrides.join('、');
            $('restart-notice').hidden=!report.restart_required;
            $('network-check').disabled=running;
        } catch {target.replaceChildren(el('p','运行检查未完成，请确认本地服务仍在运行。','error'))}
    }
    $('health-refresh').onclick=refresh;
    $('network-check').onclick=async()=>{
        if(running)return;
        running=true;$('network-check').disabled=true;
        $('network-result').textContent='正在检查 DNS、TCP 和 TLS；不会发送生成请求…';
        try{$('network-result').textContent=(await api('/audio/health/network',{})).detail}
        catch{$('network-result').textContent='检查未完成，请确认本地服务仍在运行。'}
        finally{running=false;$('network-check').disabled=false}
    };
    return {refresh};
}
