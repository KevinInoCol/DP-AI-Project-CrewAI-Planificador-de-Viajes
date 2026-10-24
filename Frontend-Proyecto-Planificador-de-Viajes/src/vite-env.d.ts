/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_MESSAGE_BUFFER_WINDOW_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
