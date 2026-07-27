'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Save, Loader2, FileText, Copy, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTranslations } from '@/lib/i18n';

export interface CoverLetterEditorProps {
  /** Cover letter content */
  content: string;
  /** Callback when content changes */
  onChange: (content: string) => void;
  /** Callback when save is triggered */
  onSave: () => void;
  /** Whether save is in progress */
  isSaving: boolean;
  /** Optional generation warnings to surface */
  warnings?: string[];
  /** Additional class names */
  className?: string;
}

export function CoverLetterEditor({
  content,
  onChange,
  onSave,
  isSaving,
  warnings,
  className,
}: CoverLetterEditorProps) {
  const { t } = useTranslations();
  const [isCopied, setIsCopied] = React.useState(false);
  const wordCount = content
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 0).length;
  const charCount = content.length;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <div className={cn('flex flex-col h-full', className)} data-testid="cover-letter-editor">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b-2 border-black bg-[#F5F5F0]">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4" />
          <h2 className="font-mono text-sm font-bold uppercase tracking-wider">
            {t('coverLetter.title')}
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-gray-500">
            {t('builder.contentStats.wordsChars', { wordCount, charCount })}
          </span>
          <Button size="sm" onClick={onSave} disabled={isSaving}>
            {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {isSaving ? t('common.saving') : t('common.save')}
          </Button>
          <Button size="sm" variant="outline" onClick={handleCopy} disabled={!content}>
            {isCopied ? (
              <>
                <Check className="w-4 h-4" />
                {t('coverLetter.copied')}
              </>
            ) : (
              <>
                <Copy className="w-4 h-4" />
                {t('coverLetter.copyToClipboard')}
              </>
            )}
          </Button>
        </div>
      </div>

      {warnings && warnings.length > 0 && (
        <div
          data-testid="cover-letter-warnings"
          className="px-4 py-2 border-b border-amber-300 bg-amber-50 font-mono text-xs text-amber-900"
        >
          {warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}

      {/* Editor Area */}
      <div className="flex-1 p-4 overflow-hidden">
        <textarea
          value={content}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t('coverLetter.editor.placeholder')}
          className={cn(
            'w-full h-full min-h-[400px] p-4',
            'font-mono text-sm leading-relaxed',
            'border-2 border-black bg-white',
            'resize-none',
            'focus:outline-none focus:ring-2 focus:ring-blue-700 focus:ring-offset-2',
            'placeholder:text-gray-400'
          )}
        />
      </div>

      {/* Footer Tips */}
      <div className="p-4 border-t border-gray-200 bg-[#F5F5F0]">
        <p className="font-mono text-xs text-gray-500">{t('coverLetter.editor.tip')}</p>
      </div>
    </div>
  );
}
