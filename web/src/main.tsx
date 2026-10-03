import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';
import { App } from './App';
import { bootstrapDocumentLocale } from './lib/i18n';

// Resolve the initial locale and apply `<html lang>` synchronously before the
// first React text render. Production stays in preview mode: an unset
// preference renders English even when the environment is Chinese. The one
// resolution is handed to <App> so the provider never re-resolves a second,
// possibly different, snapshot.
const initialLocale = bootstrapDocumentLocale();

const root = document.getElementById('root');
if (!root) throw new Error('#root not found');

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App initialLocale={initialLocale} />
  </React.StrictMode>,
);
