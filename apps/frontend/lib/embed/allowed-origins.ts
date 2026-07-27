import { DEFAULT_EMBED_PARENT_ORIGINS } from './constants';

function parseOriginList(raw: string | undefined | null): string[] {
  if (!raw?.trim()) return [];
  const out: string[] = [];
  for (const part of raw.split(',')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    try {
      out.push(new URL(trimmed).origin);
    } catch {
      // ignore malformed entries
    }
  }
  return out;
}

/** Allowlisted parent origins that may frame RM and participate in postMessage. */
export function getAllowedParentOrigins(
  envValue: string | undefined = process.env.NEXT_PUBLIC_CODEQUEST_EMBED_PARENT_ORIGINS
): string[] {
  const fromEnv = parseOriginList(envValue);
  if (fromEnv.length > 0) return [...new Set(fromEnv)];
  return [...DEFAULT_EMBED_PARENT_ORIGINS];
}

export function isAllowedParentOrigin(
  origin: string | null | undefined,
  allowlist: string[] = getAllowedParentOrigins()
): boolean {
  if (!origin) return false;
  try {
    return allowlist.includes(new URL(origin).origin);
  } catch {
    return false;
  }
}

/**
 * Resolve a postMessage targetOrigin.
 * Candidate (query/referrer/ancestor) is accepted only when already on the allowlist.
 */
export function resolveParentTargetOrigin(
  candidate: string | null | undefined,
  allowlist: string[] = getAllowedParentOrigins()
): string | null {
  if (!candidate) return null;
  try {
    const origin = new URL(candidate).origin;
    return allowlist.includes(origin) ? origin : null;
  } catch {
    return null;
  }
}
