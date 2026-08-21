/// <reference types="vite/client" />

/**
 * Typing the environment here is what stops `any` leaking out of
 * `import.meta.env` and through the API client.
 * Only VITE_-prefixed variables are exposed to the browser bundle.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
