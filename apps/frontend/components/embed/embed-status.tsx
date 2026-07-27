'use client';

import React from 'react';
import Link from 'next/link';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import AlertCircle from 'lucide-react/dist/esm/icons/alert-circle';
import AlertTriangle from 'lucide-react/dist/esm/icons/alert-triangle';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import { Button } from '@/components/ui/button';
import { useEmbedMode } from './embed-provider';

export type EmbedUiState =
  | 'ready'
  | 'loading'
  | 'backend-unavailable'
  | 'connector-unavailable'
  | 'ollama-unavailable'
  | 'model-missing'
  | 'parsing-failed';

export function EmbedStatusBanner({
  state,
  onRetry,
  settingsHref,
}: {
  state: EmbedUiState;
  onRetry?: () => void;
  settingsHref: string;
}) {
  if (state === 'ready') return null;

  if (state === 'loading') {
    return (
      <div
        data-testid="embed-state-loading"
        className="shrink-0 border-b border-black bg-white px-4 py-3 flex items-center gap-3 font-mono text-sm text-blue-700"
        role="status"
      >
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
        <span>Loading Resume Lab…</span>
      </div>
    );
  }

  if (state === 'backend-unavailable') {
    return (
      <div
        data-testid="embed-state-backend-unavailable"
        className="shrink-0 border-b-2 border-red-600 bg-red-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3"
        role="alert"
      >
        <div className="flex items-center gap-3 font-mono text-sm text-red-800">
          <AlertCircle className="w-4 h-4 shrink-0" aria-hidden />
          <span>Resume Matcher backend is unavailable. Start the API and retry.</span>
        </div>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="w-4 h-4" />
            Retry
          </Button>
        )}
      </div>
    );
  }

  if (state === 'connector-unavailable') {
    return (
      <div
        data-testid="embed-state-connector-unavailable"
        className="shrink-0 border-b-2 border-amber-600 bg-amber-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3"
        role="alert"
      >
        <div className="flex items-center gap-3 font-mono text-sm text-amber-900">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
          <span>
            Local AI (Ollama) is not ready. Pair the Code Quest Local Connector or configure Ollama
            in settings.
          </span>
        </div>
        <Link href={settingsHref}>
          <Button variant="outline" size="sm">
            Settings
          </Button>
        </Link>
      </div>
    );
  }

  if (state === 'ollama-unavailable') {
    return (
      <div
        data-testid="embed-state-ollama-unavailable"
        className="shrink-0 border-b-2 border-amber-600 bg-amber-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3"
        role="alert"
      >
        <div className="flex items-center gap-3 font-mono text-sm text-amber-900">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
          <span>
            Ollama is unreachable. Start Ollama locally, then retry. No cloud providers are enabled
            in this build.
          </span>
        </div>
        <div className="flex gap-2">
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              <RefreshCw className="w-4 h-4" />
              Retry
            </Button>
          )}
          <Link href={settingsHref}>
            <Button variant="outline" size="sm">
              Settings
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  if (state === 'model-missing') {
    return (
      <div
        data-testid="embed-state-model-missing"
        className="shrink-0 border-b-2 border-amber-600 bg-amber-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3"
        role="alert"
      >
        <div className="flex items-center gap-3 font-mono text-sm text-amber-900">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
          <span>
            The configured Ollama model is not installed. Pull the model in Settings, then retry.
          </span>
        </div>
        <Link href={settingsHref}>
          <Button variant="outline" size="sm">
            Settings
          </Button>
        </Link>
      </div>
    );
  }

  // parsing-failed
  return (
    <div
      data-testid="embed-state-parsing-failed"
      className="shrink-0 border-b-2 border-red-600 bg-red-50 px-4 py-3 flex items-center gap-3 font-mono text-sm text-red-800"
      role="alert"
    >
      <AlertCircle className="w-4 h-4 shrink-0" aria-hidden />
      <span>Resume parsing failed. Open the master resume and retry processing.</span>
    </div>
  );
}

/** Thin wrapper that reads embed context for tests / composition. */
export function EmbedStatusFromContext({
  state,
  onRetry,
}: {
  state: EmbedUiState;
  onRetry?: () => void;
}) {
  const { withEmbedHref } = useEmbedMode();
  return (
    <EmbedStatusBanner state={state} onRetry={onRetry} settingsHref={withEmbedHref('/settings')} />
  );
}
