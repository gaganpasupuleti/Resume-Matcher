import { describe, expect, it } from 'vitest';
import {
  detectEmbedMode,
  getAllowedParentOrigins,
  isAllowedParentOrigin,
  isEmbedFlagInHash,
  isEmbedFlagInSearch,
  pickParentTargetOrigin,
  resolveParentTargetOrigin,
  withEmbedParams,
} from '@/lib/embed';

describe('embed mode detection', () => {
  it('standalone: no query/hash and not framed → off', () => {
    expect(
      detectEmbedMode({
        search: '',
        hash: '',
        isFramed: false,
        sessionActive: false,
      })
    ).toBe(false);
  });

  it('activates via ?embed=codequest', () => {
    expect(isEmbedFlagInSearch('?embed=codequest')).toBe(true);
    expect(
      detectEmbedMode({
        search: '?embed=codequest',
        hash: '',
        isFramed: false,
        sessionActive: false,
      })
    ).toBe(true);
  });

  it('activates via #embed=codequest', () => {
    expect(isEmbedFlagInHash('#embed=codequest')).toBe(true);
    expect(
      detectEmbedMode({
        search: '',
        hash: '#embed=codequest&parentOrigin=http://localhost:5000',
        isFramed: false,
        sessionActive: false,
      })
    ).toBe(true);
  });

  it('keeps embed across navigation only while framed + session', () => {
    expect(
      detectEmbedMode({
        search: '',
        hash: '',
        isFramed: true,
        sessionActive: true,
      })
    ).toBe(true);
    expect(
      detectEmbedMode({
        search: '',
        hash: '',
        isFramed: false,
        sessionActive: true,
      })
    ).toBe(false);
  });

  it('withEmbedParams appends embed query', () => {
    expect(withEmbedParams('/dashboard')).toBe('/dashboard?embed=codequest');
    expect(withEmbedParams('/builder?id=abc', 'http://localhost:5000')).toBe(
      '/builder?id=abc&embed=codequest&parentOrigin=http%3A%2F%2Flocalhost%3A5000'
    );
  });
});

describe('parent origin allowlist', () => {
  const allowlist = getAllowedParentOrigins(undefined);

  it('defaults include local Code Quest origins', () => {
    expect(allowlist).toContain('http://localhost:5000');
    expect(allowlist).toContain('http://127.0.0.1:5000');
  });

  it('accepts allowlisted parentOrigin', () => {
    expect(resolveParentTargetOrigin('http://localhost:5000', allowlist)).toBe(
      'http://localhost:5000'
    );
    expect(isAllowedParentOrigin('http://127.0.0.1:5000', allowlist)).toBe(true);
  });

  it('rejects wrong parent origin', () => {
    expect(resolveParentTargetOrigin('http://evil.example', allowlist)).toBeNull();
    expect(isAllowedParentOrigin('https://attacker.test', allowlist)).toBe(false);
    expect(
      pickParentTargetOrigin({
        search: '?embed=codequest&parentOrigin=https://evil.example',
        hash: '',
        referrer: 'https://evil.example/app',
        ancestorOrigins: ['https://evil.example'],
        allowlist,
      })
    ).toBeNull();
  });

  it('honors env allowlist override', () => {
    const custom = getAllowedParentOrigins('https://codequest.example');
    expect(custom).toEqual(['https://codequest.example']);
    expect(resolveParentTargetOrigin('http://localhost:5000', custom)).toBeNull();
    expect(resolveParentTargetOrigin('https://codequest.example', custom)).toBe(
      'https://codequest.example'
    );
  });
});
