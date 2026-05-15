/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENABLE_PROTOTYPES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
