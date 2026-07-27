import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { EmbedStatusBanner } from '@/components/embed/embed-status';
import { JDAnalysisPanel } from '@/components/builder/jd-analysis-panel';
import { CoverLetterEditor } from '@/components/builder/cover-letter-editor';

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string, params?: Record<string, string | number>) => {
      if (!params) return key;
      return Object.entries(params).reduce(
        (acc, [k, v]) => acc.replace(`{${k}}`, String(v)),
        key
      );
    },
    locale: 'en',
  }),
}));

const analyzeMock = vi.hoisted(() =>
  vi.fn(async () => ({
    overall_score: 72,
    breakdown: [
      {
        id: 'skills',
        label: 'Skills',
        score: 80,
        weight: 0.4,
        reason: 'Strong skill overlap',
      },
    ],
    matched_keywords: ['Python', 'SQL'],
    missing_keywords: ['Kubernetes'],
    strengths: ['Clear experience'],
    gaps: ['No k8s'],
    recommendations: ['Add container skills'],
    warnings: [],
  }))
);

vi.mock('@/lib/api/resume', () => ({
  analyzeJdMatch: (...args: unknown[]) => analyzeMock(...args),
}));

describe('embed Ollama states', () => {
  it('shows model-missing and ollama-unavailable banners', () => {
    const { rerender } = render(
      <EmbedStatusBanner state="model-missing" settingsHref="/settings?embed=codequest" />
    );
    expect(screen.getByTestId('embed-state-model-missing')).toBeInTheDocument();

    rerender(
      <EmbedStatusBanner state="ollama-unavailable" settingsHref="/settings?embed=codequest" />
    );
    expect(screen.getByTestId('embed-state-ollama-unavailable')).toBeInTheDocument();
  });
});

describe('JD analysis panel', () => {
  beforeEach(() => {
    analyzeMock.mockClear();
  });

  it('renders score breakdown and keyword lists', async () => {
    render(<JDAnalysisPanel resumeId="r1" jobId="j1" />);

    await waitFor(() => {
      expect(screen.getByTestId('jd-analysis-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('jd-overall-score')).toHaveTextContent('72%');
    expect(screen.getByTestId('jd-score-breakdown')).toBeInTheDocument();
    expect(screen.getByTestId('jd-matched-keywords')).toHaveTextContent('Python');
    expect(screen.getByTestId('jd-missing-keywords')).toHaveTextContent('Kubernetes');
    expect(analyzeMock).toHaveBeenCalledWith('r1', 'j1', true);
  });
});

describe('cover letter editor', () => {
  it('is editable and exposes copy control', () => {
    const onChange = vi.fn();
    render(
      <CoverLetterEditor
        content="Hello recruiter"
        onChange={onChange}
        onSave={() => {}}
        isSaving={false}
        warnings={['insufficient_data']}
      />
    );
    expect(screen.getByTestId('cover-letter-editor')).toBeInTheDocument();
    expect(screen.getByTestId('cover-letter-warnings')).toHaveTextContent('insufficient_data');
    expect(screen.getByDisplayValue('Hello recruiter')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /coverLetter.copyToClipboard/i })).toBeInTheDocument();
  });
});
