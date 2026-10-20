import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',workers:1,use:{baseURL:process.env.BASE_URL??'http://127.0.0.1:18080',viewport:{width:1137,height:954},screenshot:'only-on-failure',trace:'off'},reporter:[['list'],['json',{outputFile:'test-results/results.json'}]]});
