/** Code Quest Resume Lab iframe embed contract (frontend-only; distinct from CODEQUEST_INTEGRATION_MODE). */

export const EMBED_QUERY_KEY = 'embed' as const;
export const EMBED_QUERY_VALUE = 'codequest' as const;
export const EMBED_PARENT_ORIGIN_QUERY_KEY = 'parentOrigin' as const;

/** sessionStorage key — keeps embed chrome across in-app navigations while framed */
export const EMBED_SESSION_KEY = 'rm.codequest.embed' as const;

export const EMBED_PROTOCOL = 'codequest-resume-embed/v1' as const;

/** Default Code Quest shell origins (local). Override via NEXT_PUBLIC_CODEQUEST_EMBED_PARENT_ORIGINS. */
export const DEFAULT_EMBED_PARENT_ORIGINS = [
  'http://localhost:5000',
  'http://127.0.0.1:5000',
] as const;
