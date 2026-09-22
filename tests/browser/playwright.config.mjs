import { defineConfig } from 'playwright/test';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
export default defineConfig({
    testDir:'.',testMatch:'*.spec.mjs',workers:1,fullyParallel:false,
    timeout:30000,expect:{timeout:10000},reporter:'list',
    use:{baseURL:'http://127.0.0.1:8773',headless:true,trace:'retain-on-failure',viewport:{width:1280,height:900}},
    webServer:{command:'python3 tests/browser/server.py --port 8773',cwd:root,url:'http://127.0.0.1:8773',timeout:60000,reuseExistingServer:false},
});
