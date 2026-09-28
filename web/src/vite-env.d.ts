/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENABLE_PROTOTYPES?: string;
  /** THR-118 W2c: test/evidence-only activation of Settings ▸ Preferences. */
  readonly VITE_ENABLE_I18N_PREFERENCES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
