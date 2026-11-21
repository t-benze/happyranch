import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';
import { App } from './App';
import { bootstrapDocumentLocale } from './lib/i18n';
import { appLocalePreferenceAdapter, awaitNativeLocaleSnapshot } from './lib/native/locale';

// Resolve the initial locale and apply `<html lang>` synchronously before the
// first React text render. Production uses full mode. Browser: a saved choice
// wins, otherwise supported browser languages choose the locale. Desktop
// (native authority, THR-387 S8-1-W): the first `app.status` locale, waited
// for with the existing bounded status timeout. The one resolution is handed
// to <App> so the provider never reads a second startup snapshot.
async function start(): Promise<void> {
  await awaitNativeLocaleSnapshot();
  const initialLocale = bootstrapDocumentLocale({ mode: 'full', adapter: appLocalePreferenceAdapter() });

  const root = document.getElementById('root');
  if (!root) throw new Error('#root not found');

  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App initialLocale={initialLocale} />
    </React.StrictMode>,
  );
}

void start();
