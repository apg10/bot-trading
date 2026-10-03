declare module '*.css' {
  const css: Record<string, string>
  export default css
}

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
