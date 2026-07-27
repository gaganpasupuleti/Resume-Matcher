import {
  EMBED_PARENT_ORIGIN_QUERY_KEY,
  EMBED_QUERY_KEY,
  EMBED_QUERY_VALUE,
  EMBED_SESSION_KEY,
} from './constants';
import { resolveParentTargetOrigin } from './allowed-origins';

export function isEmbedFlagInSearch(search: string): boolean {
  const raw = search.startsWith('?') ? search.slice(1) : search;
  if (!raw) return false;
  return new URLSearchParams(raw).get(EMBED_QUERY_KEY) === EMBED_QUERY_VALUE;
}

export function isEmbedFlagInHash(hash: string): boolean {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!raw) return false;
  return new URLSearchParams(raw).get(EMBED_QUERY_KEY) === EMBED_QUERY_VALUE;
}

export function readParentOriginCandidate(search: string, hash: string): string | null {
  const fromSearch = new URLSearchParams(
    search.startsWith('?') ? search.slice(1) : search
  ).get(EMBED_PARENT_ORIGIN_QUERY_KEY);
  if (fromSearch) return fromSearch;

  const fromHash = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash).get(
    EMBED_PARENT_ORIGIN_QUERY_KEY
  );
  return fromHash;
}

export function detectEmbedMode(input: {
  search: string;
  hash: string;
  isFramed: boolean;
  sessionActive: boolean;
}): boolean {
  if (isEmbedFlagInSearch(input.search) || isEmbedFlagInHash(input.hash)) {
    return true;
  }
  // Persist across in-app navigations only while still framed by a parent.
  return input.isFramed && input.sessionActive;
}

export function readEmbedSessionFlag(): boolean {
  if (typeof sessionStorage === 'undefined') return false;
  try {
    return sessionStorage.getItem(EMBED_SESSION_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeEmbedSessionFlag(active: boolean): void {
  if (typeof sessionStorage === 'undefined') return;
  try {
    if (active) sessionStorage.setItem(EMBED_SESSION_KEY, '1');
    else sessionStorage.removeItem(EMBED_SESSION_KEY);
  } catch {
    // private mode / blocked storage — ignore
  }
}

/** Append embed query params to an internal path (preserves existing search). */
export function withEmbedParams(
  path: string,
  parentOrigin: string | null = null
): string {
  const url = new URL(path, 'http://rm.local');
  url.searchParams.set(EMBED_QUERY_KEY, EMBED_QUERY_VALUE);
  if (parentOrigin) {
    url.searchParams.set(EMBED_PARENT_ORIGIN_QUERY_KEY, parentOrigin);
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Pick a validated parent target origin from URL / referrer / ancestorOrigins. */
export function pickParentTargetOrigin(input: {
  search: string;
  hash: string;
  referrer: string;
  ancestorOrigins: readonly string[];
  allowlist: string[];
}): string | null {
  const fromUrl = resolveParentTargetOrigin(
    readParentOriginCandidate(input.search, input.hash),
    input.allowlist
  );
  if (fromUrl) return fromUrl;

  if (input.referrer) {
    try {
      const fromReferrer = resolveParentTargetOrigin(
        new URL(input.referrer).origin,
        input.allowlist
      );
      if (fromReferrer) return fromReferrer;
    } catch {
      // ignore
    }
  }

  for (const ancestor of input.ancestorOrigins) {
    const resolved = resolveParentTargetOrigin(ancestor, input.allowlist);
    if (resolved) return resolved;
  }

  return null;
}
