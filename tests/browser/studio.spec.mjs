import { test,expect } from 'playwright/test';

// Every scenario runs against server.py's disposable, explicitly offline library.
async function ready(page){
    await page.locator('#service-open').filter({hasText:'Mock'}).waitFor();
    if(await page.locator('#entrance').isVisible())await page.locator('#enter').click();
}
test.beforeEach(async({page})=>{
    await page.goto('/');
    await ready(page);
});

test('global search, pagination, and playback survive a page change',async({page})=>{
    await expect(page.locator('#works .work')).toHaveCount(30);
    await page.getByRole('button',{name:'播放 测试 Beat 34',exact:true}).click();
    await page.locator('#play[aria-label="暂停"]').click();
    const src=await page.locator('#audio').getAttribute('src');
    await page.locator('#page-next').click();
    await expect(page.locator('#works .work')).toHaveCount(5);
    await expect(page.locator('#page-status')).toHaveText('31–35 / 35 首');
    await expect(page.locator('#player-title')).toHaveText('测试 Beat 34');
    await expect(page.locator('#audio')).toHaveAttribute('src',src);
    await page.locator('#search').fill('测试 Beat 20');
    await expect(page.locator('#works .work')).toHaveCount(1);
    await expect(page.locator('#works .work-title')).toHaveText('测试 Beat 20');
    await page.locator('#search').fill('does-not-exist');
    await expect(page.locator('#works .work')).toHaveCount(0);
    await expect(page.locator('#player-title')).toHaveText('测试 Beat 34');
});

test('preview does not create music and repeated confirmation creates one task',async({page})=>{
    await page.locator('[data-mode="instrumental"]').click();
    await page.locator('#title').fill('Browser creation');
    await page.locator('#raw').fill('木吉他，给旋律 Rap 留白。');
    let submissions=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/audio/tasks'))submissions++});
    await page.locator('#preview-action').click();
    await expect(page.locator('#preview-dialog')).toBeVisible();expect(submissions).toBe(0);
    await page.getByRole('button',{name:'返回编辑',exact:true}).click();
    await page.locator('#preview-action').click();
    await page.locator('#generate').click({clickCount:2});
    await expect(page.locator('#preview-dialog')).not.toBeVisible();
    await page.locator('#search').fill('Browser creation');
    await expect(page.locator('#works .work')).toHaveCount(1);
    expect(submissions).toBe(1);
    await page.reload();
    await ready(page);
    await page.locator('#search').fill('Browser creation');
    await expect(page.locator('#works .work')).toHaveCount(1);
});

test('trash and restore a test work without changing other pages',async({page})=>{
    await page.locator('#search').fill('测试 Beat 01');
    await expect(page.locator('#works .work')).toHaveCount(1);
    await page.locator('#works summary').click();
    await page.getByRole('button',{name:'移入回收站',exact:true}).click();
    await expect(page.locator('#works .work')).toHaveCount(0);
    await page.locator('nav [data-view="trash"]').click();
    await page.locator('#search').fill('测试 Beat 01');
    await expect(page.locator('#works .work')).toHaveCount(1);
    await page.locator('#works').getByRole('button',{name:'恢复',exact:true}).click();
    await expect(page.locator('#works .work')).toHaveCount(0);
    await page.locator('nav [data-view="music"]').click();
    await page.locator('#search').fill('测试 Beat 01');
    await expect(page.locator('#works .work')).toHaveCount(1);
});

test('failed task can be hidden and restored without resubmitting',async({page})=>{
    await page.locator('#search').fill('失败示例');
    await page.locator('#task-region').getByRole('button',{name:'删除记录',exact:true}).click();
    await expect(page.locator('#task-region')).toBeHidden();
    await page.locator('nav [data-view="trash"]').click();
    await page.locator('#search').fill('失败示例');
    await page.getByRole('button',{name:'恢复记录',exact:true}).click();
    await page.locator('nav [data-view="music"]').click();
    await page.locator('#search').fill('失败示例');
    await expect(page.locator('#task-region')).toContainText('生成失败');
});

test('stems, A/B loop, persistent notes, and existing-file export',async({page})=>{
    await page.locator('#search').fill('测试 Beat 00');
    await expect(page.locator('#works .work-title')).toHaveText(['测试 Beat 00']);
    await page.locator('#works').getByRole('button',{name:'音轨',exact:true}).click();
    await page.getByRole('button',{name:'伴奏 · WAV',exact:true}).click();
    await page.locator('#listening-open').click();
    await page.locator('#compare-b').selectOption({label:'测试 Beat 00 / 人声'});
    await page.locator('#switch-b').click();
    await expect(page.locator('#listen-current')).toHaveText('测试 Beat 00 / 人声');
    await page.locator('#loop-start').fill('1');await page.locator('#loop-end').fill('3');
    await page.locator('#loop-toggle').click();await expect(page.locator('#loop-toggle')).toHaveAttribute('aria-pressed','true');
    await page.locator('#note-text').fill('自动回归：检查这一段');await page.locator('#note-save').click();
    await expect(page.locator('#listening-notes')).toContainText('自动回归：检查这一段');
    await page.reload();
    await ready(page);
    await page.locator('#search').fill('测试 Beat 00');
    await expect(page.locator('#works .work-title')).toHaveText(['测试 Beat 00']);
    await page.locator('#works').getByRole('button',{name:'音轨',exact:true}).click();
    await page.getByRole('button',{name:'人声 · WAV',exact:true}).click();await page.locator('#listening-open').click();
    await expect(page.locator('#listening-notes')).toContainText('自动回归：检查这一段');
    await page.getByRole('button',{name:'关闭试听工具',exact:true}).click();
    const download=page.waitForEvent('download');await page.getByRole('link',{name:'打包导出已保存文件',exact:true}).click();
    expect((await download).suggestedFilename()).toContain('.zip');
});

test('export button and help remain separate on desktop and mobile',async({page})=>{
    await page.locator('nav [data-view="music"]').click();
    await page.locator('#search').fill('测试 Beat 00');
    await expect(page.locator('#works .work-title')).toHaveText(['测试 Beat 00']);
    await page.locator('#works').getByRole('button',{name:'音轨',exact:true}).click();
    for(const width of [1280,390]){
        await page.setViewportSize({width,height:900});
        const action=page.getByRole('link',{name:'打包导出已保存文件',exact:true});
        await expect(action).toBeVisible();
        const buttonBox=await action.boundingBox();
        const helpBox=await page.locator('.work-details>.help').boundingBox();
        expect(helpBox.y).toBeGreaterThanOrEqual(buttonBox.y+buttonBox.height+8);
        expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
});
