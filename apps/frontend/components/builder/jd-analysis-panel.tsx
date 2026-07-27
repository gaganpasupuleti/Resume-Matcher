'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { Loader2, AlertTriangle, Target, CheckCircle2, XCircle } from 'lucide-react';
import { analyzeJdMatch, type JDAnalysisResult } from '@/lib/api/resume';
import { Button } from '@/components/ui/button';
import { useTranslations } from '@/lib/i18n';

interface JDAnalysisPanelProps {
  resumeId: string;
  jobId: string;
}

export function JDAnalysisPanel({ resumeId, jobId }: JDAnalysisPanelProps) {
  const { t } = useTranslations();
  const [analysis, setAnalysis] = useState<JDAnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await analyzeJdMatch(resumeId, jobId, true);
      setAnalysis(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('builder.jdMatch.analysisFailed'));
      setAnalysis(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // ponytail: re-run when resume/job identity changes only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeId, jobId]);

  if (loading && !analysis) {
    return (
      <div
        data-testid="jd-analysis-loading"
        className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 bg-white font-mono text-sm text-blue-700"
        role="status"
      >
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
        <span>{t('builder.jdMatch.analyzing')}</span>
      </div>
    );
  }

  if (error && !analysis) {
    return (
      <div
        data-testid="jd-analysis-error"
        className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-amber-300 bg-amber-50 font-mono text-sm text-amber-900"
        role="alert"
      >
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  if (!analysis) return null;

  const scorePct = Math.round(analysis.overall_score);

  return (
    <div data-testid="jd-analysis-panel" className="border-b border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <Target className="w-4 h-4 text-blue-700" aria-hidden />
          <span className="font-mono text-sm font-bold uppercase tracking-wide">
            {t('builder.jdMatch.overallScore')}
          </span>
          <span
            data-testid="jd-overall-score"
            className={`text-xl font-bold ${
              scorePct >= 70 ? 'text-green-700' : scorePct >= 40 ? 'text-amber-600' : 'text-red-600'
            }`}
          >
            {scorePct}%
          </span>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          {t('builder.jdMatch.refreshAnalysis')}
        </Button>
      </div>

      {analysis.breakdown?.length > 0 && (
        <div
          data-testid="jd-score-breakdown"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 px-4 pb-3"
        >
          {analysis.breakdown.map((item) => (
            <div
              key={item.id}
              className="border border-gray-200 bg-[#F5F5F0] px-3 py-2 font-mono text-xs"
            >
              <div className="flex justify-between gap-2 font-bold">
                <span>{item.label}</span>
                <span>{Math.round(item.score)}</span>
              </div>
              <p className="mt-1 text-gray-600 leading-snug">{item.reason}</p>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 px-4 pb-3">
        <KeywordList
          testId="jd-matched-keywords"
          icon={<CheckCircle2 className="w-3.5 h-3.5 text-green-700" aria-hidden />}
          title={t('builder.jdMatch.matchedKeywords')}
          items={analysis.matched_keywords}
          empty={t('builder.jdMatch.noMatchedKeywords')}
          tone="matched"
        />
        <KeywordList
          testId="jd-missing-keywords"
          icon={<XCircle className="w-3.5 h-3.5 text-red-600" aria-hidden />}
          title={t('builder.jdMatch.missingKeywords')}
          items={analysis.missing_keywords}
          empty={t('builder.jdMatch.noMissingKeywords')}
          tone="missing"
        />
      </div>

      {(analysis.warnings?.length ?? 0) > 0 && (
        <div
          data-testid="jd-analysis-warnings"
          className="px-4 pb-3 font-mono text-xs text-amber-800"
        >
          {analysis.warnings!.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function KeywordList({
  testId,
  icon,
  title,
  items,
  empty,
  tone,
}: {
  testId: string;
  icon: ReactNode;
  title: string;
  items: string[];
  empty: string;
  tone: 'matched' | 'missing';
}) {
  return (
    <div data-testid={testId}>
      <div className="flex items-center gap-1.5 font-mono text-xs font-bold uppercase mb-1.5">
        {icon}
        <span>{title}</span>
        <span className="text-gray-500">({items.length})</span>
      </div>
      {items.length === 0 ? (
        <p className="font-mono text-xs text-gray-500">{empty}</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {items.map((kw) => (
            <span
              key={kw}
              className={`px-2 py-0.5 text-xs font-mono border ${
                tone === 'matched'
                  ? 'bg-green-50 border-green-300 text-green-800'
                  : 'bg-red-50 border-red-300 text-red-800'
              }`}
            >
              {kw}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
