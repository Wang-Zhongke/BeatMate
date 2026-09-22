import { $, el, button, clock } from './common.js';

export function loopRange(start,end,duration){
    const a=Number(start), b=Number(end);
    return start!==''&&end!==''&&Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b>a&&b<=duration ? {start:a,end:b} : null;
}

export function setupListening({api,audio,getWorks,getCurrent,playAsset,toast}){
    let signature='', noteAsset=null, loop=null, saving=false;
    const roles={mix:'整曲',instrumental:'伴奏',vocals:'人声'};
    function choices(){return getWorks().filter(w=>!w.deleted).flatMap(work=>work.tracks.filter(a=>a.available).map(asset=>({work,asset})))}
    function active(){return choices().find(x=>x.asset.id===getCurrent()?.asset.id)}
    async function showNotes(id){
        try{
            const notes=await api('/audio/assets/'+id+'/notes');
            if(noteAsset!==id)return;
            $('listening-notes').replaceChildren(...notes.map(note=>{
                const row=el('div',null,'listen-note');
                const seek=button(clock(note.seconds),()=>{if(getCurrent()?.asset.id===id)audio.currentTime=note.seconds},'quiet');
                const remove=button('删除备注',async()=>{
                    remove.disabled=true;
                    try{await api('/audio/assets/'+id+'/notes',{action:'delete',note_id:note.id});await showNotes(id)}
                    catch(e){toast(e.message);remove.disabled=false}
                },'icon-button','trash');
                row.append(seek,el('span',note.text),remove);return row;
            }));
            if(!notes.length)$('listening-notes').append(el('p','听到需要调整的地方，记下当前时间。','help'));
        }catch(e){if(noteAsset===id)$('listening-notes').replaceChildren(el('p',e.message,'error'))}
    }
    function sync(){
        const items=choices();const next=JSON.stringify(items.map(x=>[x.asset.id,x.work.title]));
        if(next!==signature){
            signature=next;
            for(const side of ['a','b']){
                const select=$('compare-'+side),old=select.value;
                select.replaceChildren(new Option('选择作品与音轨',''));
                for(const {work,asset} of items)select.add(new Option(work.title+' / '+(roles[asset.track_type]||'音频'),asset.id));
                select.value=items.some(x=>x.asset.id===old)?old:'';
            }
        }
        const current=getCurrent();const item=active();
        $('listen-current').textContent=item?item.work.title+' / '+(roles[item.asset.track_type]||'音频'):'先播放一条音轨';
        $('listen-play').disabled=!item;
        $('listen-play').textContent=audio.paused?'播放':'暂停';
        for(const id of ['loop-start-now','loop-end-now','loop-toggle','listen-seek'])$(id).disabled=!item;
        $('note-save').disabled=!item||saving;
        for(const side of ['a','b']){
            $('switch-'+side).disabled=!$('compare-'+side).value;
            $('switch-'+side).setAttribute('aria-pressed',String(!!current&&current.asset.id===$('compare-'+side).value));
        }
        if(loop&&(!item||loop.end>item.asset.duration_seconds)){loop=null;updateLoop();$('loop-message').textContent='音轨时长已改变，循环已关闭。'}
        if(noteAsset!==(item?.asset.id||null)){
            noteAsset=item?.asset.id||null;$('note-text').value='';$('listening-notes').replaceChildren();
            if(noteAsset&&$('listening-dialog').open)showNotes(noteAsset);
        }
    }
    $('listening-open').onclick=()=>{
        $('listening-dialog').showModal();sync();
        if(getCurrent()&&!$('compare-a').value)$('compare-a').value=getCurrent().asset.id;
        sync();if(noteAsset)showNotes(noteAsset);
    };
    for(const side of ['a','b']){
        $('compare-'+side).onchange=sync;
        $('switch-'+side).onclick=()=>{
            const item=choices().find(x=>x.asset.id===$('compare-'+side).value);
            if(item){playAsset(item.work,item.asset,{time:audio.ended?0:audio.currentTime||0});sync()}
        };
    }
    $('listen-play').onclick=async()=>{if(audio.paused){try{await audio.play()}catch{toast('暂时无法播放这条音轨。')}}else audio.pause()};
    function updateLoop(){ $('loop-toggle').setAttribute('aria-pressed',String(!!loop));$('listening-open').classList.toggle('loop-active',!!loop);$('listening-open').title=loop?'片段循环中，点击调整或关闭':'对比与试听备注'; }
    function resetLoop(){loop=null;updateLoop();$('loop-message').textContent='范围已修改，点击启用循环。'}
    $('loop-start-now').onclick=()=>{$('loop-start').value=audio.currentTime.toFixed(2);resetLoop()};
    $('loop-end-now').onclick=()=>{$('loop-end').value=audio.currentTime.toFixed(2);resetLoop()};
    $('loop-toggle').onclick=()=>{
        if(loop){loop=null;$('loop-message').textContent='循环已关闭。'}
        else{
            loop=loopRange($('loop-start').value,$('loop-end').value,getCurrent()?.asset.duration_seconds||0);
            $('loop-message').textContent=loop?`循环 ${clock(loop.start)} – ${clock(loop.end)}`:'请填写音轨时长内的起止秒数，结束须大于开始。';
            if(loop)audio.currentTime=loop.start;
        }
        updateLoop();
    };
    for(const id of ['loop-start','loop-end'])$(id).oninput=resetLoop;
    $('listen-seek').oninput=()=>{if(Number.isFinite(audio.duration))audio.currentTime=Number($('listen-seek').value)/1000*audio.duration};
    audio.addEventListener('timeupdate',()=>{
        if(loop&&audio.currentTime>=loop.end)audio.currentTime=loop.start;
        $('listen-time').textContent=clock(audio.currentTime);
        $('listen-seek').value=Number.isFinite(audio.duration)?audio.currentTime/audio.duration*1000:0;
    });
    audio.addEventListener('ended',async()=>{if(loop){audio.currentTime=loop.start;try{await audio.play()}catch{toast('循环播放已暂停，请点击播放继续。')}}});
    $('note-save').onclick=async()=>{
        const item=active();if(!item||saving)return;
        const text=$('note-text').value.trim();if(!text){toast('先写下这段声音需要调整的地方。');return}
        saving=true;$('note-save').disabled=true;
        const id=item.asset.id;
        try{
            await api('/audio/assets/'+id+'/notes',{action:'add',seconds:Math.min(audio.currentTime,item.asset.duration_seconds),text});
            if(noteAsset===id){$('note-text').value='';await showNotes(id)}
            toast('时间点备注已保存到音乐库。');
        }catch(e){toast(e.message)}finally{saving=false;$('note-save').disabled=!active()}
    };
    return {sync, isLooping:()=>!!loop, selectedAssetIds:()=>[$('compare-a').value,$('compare-b').value].filter(Boolean)};
}
