import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SwissGrid } from '@/components/home/swiss-grid';
import { EmbedStatusBanner } from '@/components/embed/embed-status';

vi.mock('next/image', () => ({
  default: (props: { alt?: string }) => <img alt={props.alt || ''} />,
}));

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string) => key,
    locale: 'en',
  }),
}));

const embedMock = vi.hoisted(() => ({
  isEmbedMode: false,
  withEmbedHref: (path: string) => `${path}?embed=codequest`,
  parentTargetOrigin: null as string | null,
  parentAcknowledged: false,
  uiState: 'ready' as const,
}));

vi.mock('@/components/embed/embed-provider', () => ({
  useEmbedMode: () => embedMock,
}));

describe('standalone chrome', () => {
  it('renders dashboard header and footer branding', () => {
    embedMock.isEmbedMode = false;
    render(
      <SwissGrid>
        <div data-testid="feature-card">Master</div>
      </SwissGrid>
    );
    expect(screen.getByTestId('standalone-swiss-grid')).toBeInTheDocument();
    expect(screen.getByTestId('standalone-dashboard-header')).toBeInTheDocument();
    expect(screen.getByTestId('standalone-dashboard-footer')).toBeInTheDocument();
    expect(screen.getByTestId('resume-feature-grid')).toBeInTheDocument();
    expect(screen.getByTestId('feature-card')).toBeInTheDocument();
  });
});

describe('embed chrome', () => {
  it('hides duplicate standalone header/footer and keeps feature grid + settings', () => {
    embedMock.isEmbedMode = true;
    render(
      <SwissGrid>
        <div data-testid="feature-card">Master</div>
      </SwissGrid>
    );
    expect(screen.getByTestId('embed-swiss-grid')).toBeInTheDocument();
    expect(screen.queryByTestId('standalone-dashboard-header')).not.toBeInTheDocument();
    expect(screen.queryByTestId('standalone-dashboard-footer')).not.toBeInTheDocument();
    expect(screen.getByTestId('resume-feature-grid')).toBeInTheDocument();
    expect(screen.getByTestId('embed-inner-settings')).toBeInTheDocument();
    expect(screen.getByTestId('feature-card')).toBeInTheDocument();
  });
});

describe('embed status states', () => {
  it('shows connector unavailable', () => {
    render(
      <EmbedStatusBanner
        state="connector-unavailable"
        settingsHref="/settings?embed=codequest"
      />
    );
    expect(screen.getByTestId('embed-state-connector-unavailable')).toBeInTheDocument();
  });

  it('shows backend unavailable', () => {
    render(<EmbedStatusBanner state="backend-unavailable" settingsHref="/settings" onRetry={() => {}} />);
    expect(screen.getByTestId('embed-state-backend-unavailable')).toBeInTheDocument();
  });

  it('shows loading and parsing-failed', () => {
    const { rerender } = render(
      <EmbedStatusBanner state="loading" settingsHref="/settings" />
    );
    expect(screen.getByTestId('embed-state-loading')).toBeInTheDocument();
    rerender(<EmbedStatusBanner state="parsing-failed" settingsHref="/settings" />);
    expect(screen.getByTestId('embed-state-parsing-failed')).toBeInTheDocument();
  });

  it('shows ollama unavailable and model missing', () => {
    const { rerender } = render(
      <EmbedStatusBanner state="ollama-unavailable" settingsHref="/settings" />
    );
    expect(screen.getByTestId('embed-state-ollama-unavailable')).toBeInTheDocument();
    rerender(<EmbedStatusBanner state="model-missing" settingsHref="/settings" />);
    expect(screen.getByTestId('embed-state-model-missing')).toBeInTheDocument();
  });

  it('renders nothing when ready', () => {
    const { container } = render(
      <EmbedStatusBanner state="ready" settingsHref="/settings" />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
