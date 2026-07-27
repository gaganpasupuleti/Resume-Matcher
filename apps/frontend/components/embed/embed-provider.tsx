'use client';

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  Suspense,
} from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { useStatusCache } from '@/lib/context/status-cache';
import { fetchResume } from '@/lib/api/resume';
import {
  detectEmbedMode,
  getAllowedParentOrigins,
  handleParentMessageEvent,
  pickParentTargetOrigin,
  postToParent,
  buildReadyMessage,
  readEmbedSessionFlag,
  withEmbedParams,
  writeEmbedSessionFlag,
} from '@/lib/embed';
import { EmbedStatusBanner, type EmbedUiState } from './embed-status';

type EmbedContextValue = {
  isEmbedMode: boolean;
  parentTargetOrigin: string | null;
  parentAcknowledged: boolean;
  withEmbedHref: (path: string) => string;
  uiState: EmbedUiState;
};

const EmbedContext = createContext<EmbedContextValue>({
  isEmbedMode: false,
  parentTargetOrigin: null,
  parentAcknowledged: false,
  withEmbedHref: (path) => path,
  uiState: 'ready',
});

export function useEmbedMode(): EmbedContextValue {
  return useContext(EmbedContext);
}

function useIsFramed(): boolean {
  const [framed, setFramed] = useState(false);
  useEffect(() => {
    setFramed(window.self !== window.top);
  }, []);
  return framed;
}

function EmbedProviderInner({ children }: { children: React.ReactNode }) {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const isFramed = useIsFramed();
  const search = searchParams?.toString() ? `?${searchParams.toString()}` : '';
  const [hash, setHash] = useState('');
  const [sessionActive, setSessionActive] = useState(false);
  const [parentTargetOrigin, setParentTargetOrigin] = useState<string | null>(null);
  const [parentAcknowledged, setParentAcknowledged] = useState(false);
  const [parsingFailed, setParsingFailed] = useState(false);

  const {
    status: systemStatus,
    isLoading: statusLoading,
    error: statusError,
    refreshStatus,
  } = useStatusCache();

  useEffect(() => {
    setHash(typeof window !== 'undefined' ? window.location.hash : '');
    setSessionActive(readEmbedSessionFlag());
  }, [pathname, search]);

  const allowlist = useMemo(() => getAllowedParentOrigins(), []);

  const isEmbedMode = useMemo(
    () =>
      detectEmbedMode({
        search,
        hash,
        isFramed,
        sessionActive,
      }),
    [search, hash, isFramed, sessionActive]
  );

  useEffect(() => {
    if (!isEmbedMode) {
      if (!isFramed) writeEmbedSessionFlag(false);
      return;
    }
    writeEmbedSessionFlag(true);
    setSessionActive(true);

    const ancestorOrigins =
      typeof window !== 'undefined' && 'ancestorOrigins' in location
        ? Array.from((location as Location & { ancestorOrigins: DOMStringList }).ancestorOrigins)
        : [];

    const target = pickParentTargetOrigin({
      search,
      hash,
      referrer: typeof document !== 'undefined' ? document.referrer : '',
      ancestorOrigins,
      allowlist,
    });
    setParentTargetOrigin(target);

    if (target) {
      postToParent(buildReadyMessage(), target);
    }
  }, [isEmbedMode, isFramed, search, hash, allowlist]);

  useEffect(() => {
    if (!isEmbedMode) return;

    const onMessage = (event: MessageEvent) => {
      const ack = handleParentMessageEvent(event, allowlist);
      if (!ack) return;
      const target = resolveTargetForEvent(event.origin, parentTargetOrigin, allowlist);
      if (!target) return;
      postToParent(ack, target);
      setParentAcknowledged(true);
    };

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [isEmbedMode, allowlist, parentTargetOrigin]);

  // Master resume parse failure (embed status surface)
  useEffect(() => {
    if (!isEmbedMode) {
      setParsingFailed(false);
      return;
    }
    let cancelled = false;
    const masterId =
      typeof localStorage !== 'undefined' ? localStorage.getItem('master_resume_id') : null;
    if (!masterId) {
      setParsingFailed(false);
      return;
    }
    fetchResume(masterId)
      .then((data) => {
        if (cancelled) return;
        setParsingFailed(data.raw_resume?.processing_status === 'failed');
      })
      .catch(() => {
        if (!cancelled) setParsingFailed(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isEmbedMode, pathname, systemStatus?.has_master_resume]);

  const withEmbedHref = useCallback(
    (path: string) => {
      if (!isEmbedMode) return path;
      return withEmbedParams(path, parentTargetOrigin);
    },
    [isEmbedMode, parentTargetOrigin]
  );

  const uiState: EmbedUiState = useMemo(() => {
    if (!isEmbedMode) return 'ready';
    if (statusLoading && !systemStatus) return 'loading';
    if (statusError && !systemStatus) return 'backend-unavailable';
    if (systemStatus && !systemStatus.llm_configured) return 'connector-unavailable';
    if (parsingFailed) return 'parsing-failed';
    return 'ready';
  }, [isEmbedMode, statusLoading, statusError, systemStatus, parsingFailed]);

  const value = useMemo(
    () => ({
      isEmbedMode,
      parentTargetOrigin,
      parentAcknowledged,
      withEmbedHref,
      uiState,
    }),
    [isEmbedMode, parentTargetOrigin, parentAcknowledged, withEmbedHref, uiState]
  );

  return (
    <EmbedContext.Provider value={value}>
      <div
        className={isEmbedMode ? 'h-dvh w-full flex flex-col overflow-hidden bg-[#F0F0E8]' : 'contents'}
        data-testid={isEmbedMode ? 'embed-root' : 'standalone-root'}
        data-embed-mode={isEmbedMode ? 'codequest' : 'standalone'}
      >
        {isEmbedMode && (
          <EmbedStatusBanner
            state={uiState}
            onRetry={() => void refreshStatus()}
            settingsHref={withEmbedHref('/settings')}
          />
        )}
        <div className={isEmbedMode ? 'flex-1 min-h-0 overflow-hidden' : undefined}>{children}</div>
      </div>
    </EmbedContext.Provider>
  );
}

function resolveTargetForEvent(
  eventOrigin: string,
  knownTarget: string | null,
  allowlist: string[]
): string | null {
  if (knownTarget && knownTarget === eventOrigin) return knownTarget;
  return allowlist.includes(eventOrigin) ? eventOrigin : null;
}

export function EmbedProvider({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={children}>
      <EmbedProviderInner>{children}</EmbedProviderInner>
    </Suspense>
  );
}
