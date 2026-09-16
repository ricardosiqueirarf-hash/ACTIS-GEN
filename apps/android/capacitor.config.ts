import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.actisgen.mobile',
  appName: 'ACTIS GEN',
  webDir: 'www',
  server: {
    hostname: 'localhost',
    androidScheme: 'https',
  },
  android: {
    allowMixedContent: true,
    backgroundColor: '#08090b',
  },
};

export default config;
