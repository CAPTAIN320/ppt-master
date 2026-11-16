import '@analyzer/integration-sdk/ui.css';
import './styles.css';
import { createApi, defineIntegration, mountRoot } from '@analyzer/integration-sdk';
import { createRoot } from 'react-dom/client';
import { App } from './App.jsx';

export default defineIntegration({
  mount(el, ctx) {
    const root = mountRoot(el, ctx);
    const api = createApi(ctx);
    const reactRoot = createRoot(root);
    reactRoot.render(<App api={api} ctx={ctx} />);
    return () => reactRoot.unmount();
  },
});
