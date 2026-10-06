import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';
import { App } from './App';
import { bootstrapDocumentLocale } from './lib/i18n';

// Resolve the initial locale and apply `<html lang>` synchronously before the
// first React text render. Production uses full mode: a saved choice wins,
// otherwise supported browser languages choose the locale. The one resolution
// is handed to <App> so the provider never reads a second startup snapshot.
const initialLocale = bootstrapDocumentLocale({ mode: 'full' });

const root = document.getElementById('root');
if (!root) throw new Error('#root not found');

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App initialLocale={initialLocale} />
  </React.StrictMode>,
);
