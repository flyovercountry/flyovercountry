import { defineConfig } from '@playwright/test';

// safe-area.spec.js emulates device insets through Chromium's DevTools
// protocol, which WebKit does not offer.
const MOBILE_CHROMIUM_ONLY = ['mobile-panel.spec.js', 'mobile-panel-controls.spec.js', 'portrait-sheet.spec.js', 'safe-area.spec.js'];
const MOBILE_WEBKIT_ONLY = ['guided-tour.spec.js'];
const MOBILE_BOTH = ['mobile-portrait.spec.js', 'panel-affordance.spec.js'];
// Runs on the mobile projects AND desktop (behaviour must match everywhere).
const EVERY_POINTER = ['mobile-deselect.spec.js', 'journey.spec.js'];
// Short phones (iPhone SE / 8 class): the layouts that break first.
const SMALL_WEBKIT = ['tour-lowres.spec.js'];
const MOBILE_FILES = [...MOBILE_CHROMIUM_ONLY, ...MOBILE_WEBKIT_ONLY, ...MOBILE_BOTH, ...SMALL_WEBKIT];
// Opens a real adsbexchange tab. Excluded unless RUN_MANUAL is set:
//   docker compose run --rm -e RUN_MANUAL=1 playwright npx playwright test _manual-realtab-check
const MANUAL_ONLY = process.env.RUN_MANUAL ? [] : ['_manual-realtab-check.spec.js'];

export const PIXEL7_PORTRAIT = {
  viewport: { width: 412, height: 839 },
  userAgent: 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.92 Mobile Safari/537.36',
  deviceScaleFactor: 2.625,
  isMobile: true,
  hasTouch: true,
};

export const IPHONE13_PORTRAIT = {
  viewport: { width: 390, height: 844 },
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5 Mobile/15E148 Safari/604.1',
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
};

export const IPHONE8_PORTRAIT = {
  ...IPHONE13_PORTRAIT,
  viewport: { width: 375, height: 667 },
  deviceScaleFactor: 2,
};

export const IPHONESE_PORTRAIT = {
  ...IPHONE13_PORTRAIT,
  viewport: { width: 320, height: 568 },
  deviceScaleFactor: 2,
};

// Safari "page zoom" / larger-text settings shrink the CSS viewport rather
// than scale the screen: an iPhone SE at 130% lays out as 246x437 CSS px.
// These stand in for the big-font small-phone configurations.
export const IPHONESE_ZOOM130 = {
  ...IPHONESE_PORTRAIT,
  viewport: { width: 246, height: 437 },
  deviceScaleFactor: 2.6,
};

export const IPHONE8_ZOOM125 = {
  ...IPHONE8_PORTRAIT,
  viewport: { width: 300, height: 534 },
  deviceScaleFactor: 2.5,
};

export default defineConfig({
  testDir: './test/frontend',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  retries: 1,
  workers: 2,
  reporter: [['list']],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8150',
    actionTimeout: 30_000,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } },
      testIgnore: [...MOBILE_FILES, ...MANUAL_ONLY],
    },
    {
      name: 'mobile-chromium',
      use: { browserName: 'chromium', ...PIXEL7_PORTRAIT },
      testMatch: [...MOBILE_CHROMIUM_ONLY, ...MOBILE_BOTH, ...EVERY_POINTER],
    },
    {
      name: 'mobile-webkit',
      use: { browserName: 'webkit', ...IPHONE13_PORTRAIT },
      testMatch: [...MOBILE_WEBKIT_ONLY, ...MOBILE_BOTH, ...EVERY_POINTER],
    },
    {
      name: 'mobile-webkit-iphone8',
      use: { browserName: 'webkit', ...IPHONE8_PORTRAIT },
      testMatch: SMALL_WEBKIT,
    },
    {
      name: 'mobile-webkit-iphonese',
      use: { browserName: 'webkit', ...IPHONESE_PORTRAIT },
      // The smallest phone also runs the full journeys.
      testMatch: [...SMALL_WEBKIT, 'journey.spec.js'],
    },
    {
      name: 'mobile-webkit-iphonese-zoom130',
      use: { browserName: 'webkit', ...IPHONESE_ZOOM130 },
      testMatch: SMALL_WEBKIT,
    },
    {
      name: 'mobile-webkit-iphone8-zoom125',
      use: { browserName: 'webkit', ...IPHONE8_ZOOM125 },
      testMatch: SMALL_WEBKIT,
    },
  ],
});
