// import { hmrPlugin, presets } from '@open-wc/dev-server-hmr';

/** Use Hot Module replacement by adding --hmr to the start command */
const hmr = process.argv.includes('--hmr');

export default /** @type {import('@web/dev-server').DevServerConfig} */ ({
  open: '/',
  watch: !hmr,
  /** Resolve bare module imports */
  nodeResolve: {
    exportConditions: ['browser', 'development'],
  },
  mimeTypes: {
    '**/*.py': 'application/javascript',
  },
  
  /** Compile JS for older browsers. Requires @web/dev-server-esbuild plugin */
  // esbuildTarget: 'auto'

  /** Set appIndex to enable SPA routing */
  appIndex: './index.html',

  plugins: [
    {
      name: 'py-import-wrapper',
      transform(context) {
        if (context.request.url.endsWith(".py")) {
          context.response.body = `const text = ${JSON.stringify(context.response.body)};\nexport default text;`
        }
      },
    },
  ],

  // See documentation for all available options
});
