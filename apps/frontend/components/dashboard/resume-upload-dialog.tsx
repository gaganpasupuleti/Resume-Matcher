'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import {
  UploadIcon,
  Loader2Icon,
  AlertCircleIcon,
  FileIcon,
  XIcon,
  CheckCircle2Icon,
} from 'lucide-react';
import { useFileUpload, formatBytes } from '@/hooks/use-file-upload';
import { getUploadUrl } from '@/lib/api/client';
import { useTranslations } from '@/lib/i18n';
import { retryProcessing, type ResumeUploadResponse } from '@/lib/api/resume';

interface ResumeUploadDialogProps {
  trigger?: React.ReactNode;
  onUploadComplete?: (resumeId: string) => void;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

type UploadPhase = 'idle' | 'uploading' | 'extracting' | 'ai' | 'done';

const ACCEPTED_FILE_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
];
const MAX_FILE_SIZE = 4 * 1024 * 1024; // 4MB

export function ResumeUploadDialog({
  trigger,
  onUploadComplete,
  open: controlledOpen,
  onOpenChange,
}: ResumeUploadDialogProps) {
  const { t } = useTranslations();
  const [internalOpen, setInternalOpen] = useState(false);
  const [uploadFeedback, setUploadFeedback] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [failedResumeId, setFailedResumeId] = useState<string | null>(null);
  const [isRetryingProcessing, setIsRetryingProcessing] = useState(false);
  const [phase, setPhase] = useState<UploadPhase>('idle');
  const [lastDiagnostics, setLastDiagnostics] = useState<Partial<ResumeUploadResponse> | null>(
    null
  );
  const phaseTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : internalOpen;
  const setIsOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen);
    }
    onOpenChange?.(nextOpen);
  };

  const UPLOAD_URL = getUploadUrl();

  useEffect(() => {
    return () => {
      phaseTimersRef.current.forEach(clearTimeout);
      phaseTimersRef.current = [];
    };
  }, []);

  const clearPhaseTimers = () => {
    phaseTimersRef.current.forEach(clearTimeout);
    phaseTimersRef.current = [];
  };

  const startProgressPhases = () => {
    clearPhaseTimers();
    setPhase('uploading');
    setLastDiagnostics(null);
    // Backend does extract then AI in one request — stage the UI so students see both.
    phaseTimersRef.current.push(setTimeout(() => setPhase('extracting'), 400));
    phaseTimersRef.current.push(setTimeout(() => setPhase('ai'), 1600));
  };

  const handleUploadSuccess = ({
    resumeId,
    fileId,
    message,
  }: {
    resumeId: string;
    fileId?: string;
    message: string;
  }) => {
    clearPhaseTimers();
    setPhase('done');
    setUploadFeedback({ type: 'success', message });
    setFailedResumeId(null);

    setTimeout(() => {
      onUploadComplete?.(resumeId);
    }, 0);

    setTimeout(() => {
      setIsOpen(false);
      setUploadFeedback(null);
      setFailedResumeId(null);
      setPhase('idle');
      setLastDiagnostics(null);
      if (fileId) {
        removeFile(fileId);
      }
    }, 1500);
  };

  const [
    { files, isDragging, errors, isUploadingGlobal },
    {
      getInputProps,
      openFileDialog,
      removeFile,
      handleDragEnter,
      handleDragLeave,
      handleDragOver,
      handleDrop,
    },
  ] = useFileUpload({
    maxSize: MAX_FILE_SIZE,
    accept: ACCEPTED_FILE_TYPES.join(','),
    multiple: false,
    uploadUrl: UPLOAD_URL,
    onUploadSuccess: (uploadedFile, response) => {
      clearPhaseTimers();
      const data = response as ResumeUploadResponse;
      setLastDiagnostics({
        reason_code: data.reason_code,
        extraction_usable: data.extraction_usable,
        ocr_needed: data.ocr_needed,
        ai_normalization_status: data.ai_normalization_status,
        section_hints: data.section_hints,
        char_count: data.char_count,
        message: data.message,
      });

      if (data.resume_id) {
        const processingFailed = data.processing_status === 'failed';
        const successMessage = data.is_master
          ? t('dashboard.uploadDialog.successMaster')
          : t('dashboard.uploadDialog.success');
        if (processingFailed) {
          setPhase('done');
          const keptExtract =
            data.extraction_usable === true ||
            data.ai_normalization_status === 'failed' ||
            data.ai_normalization_status === 'unavailable';
          setUploadFeedback({
            type: 'error',
            message: keptExtract
              ? t('dashboard.uploadDialog.aiFailedKeptExtract')
              : t('dashboard.uploadDialog.parsingFailedKeepOpen'),
          });
          setFailedResumeId(data.resume_id);
          return;
        }
        handleUploadSuccess({
          resumeId: data.resume_id,
          fileId: uploadedFile.id,
          message: successMessage,
        });
      } else {
        setPhase('done');
        setFailedResumeId(null);
        setUploadFeedback({
          type: 'error',
          message: t('dashboard.uploadDialog.successMissingId'),
        });
      }
    },
    onUploadError: (file, errorMsg) => {
      clearPhaseTimers();
      setPhase('done');
      setFailedResumeId(null);
      setLastDiagnostics(null);
      setUploadFeedback({
        type: 'error',
        message: errorMsg || t('dashboard.uploadDialog.failed'),
      });
    },
    onFilesChange: (currentFiles) => {
      if (currentFiles.length === 0) {
        setUploadFeedback(null);
        setFailedResumeId(null);
        setPhase('idle');
        setLastDiagnostics(null);
      } else if (!isUploadingGlobal && phase === 'idle') {
        // File selected — wait for upload start
      }
    },
  });

  // When upload begins, advance staged progress.
  useEffect(() => {
    if (isUploadingGlobal && phase === 'idle') {
      startProgressPhases();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isUploadingGlobal]);

  const currentFile = files[0];
  const displayErrors = uploadFeedback?.type === 'error' ? [uploadFeedback.message] : errors;
  const preventDropzoneInteraction = (e: React.DragEvent<HTMLElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleRetryProcessing = async () => {
    if (!failedResumeId) return;
    const resumeIdToRetry = failedResumeId;
    const fileIdToRemove = currentFile?.id;
    setIsRetryingProcessing(true);
    setPhase('ai');
    try {
      const result = await retryProcessing(resumeIdToRetry);
      setLastDiagnostics({
        reason_code: result.reason_code,
        extraction_usable: result.extraction_usable,
        ocr_needed: result.ocr_needed,
        ai_normalization_status: result.ai_normalization_status,
        section_hints: result.section_hints,
        char_count: result.char_count,
        message: result.message,
      });
      if (result.processing_status !== 'ready') {
        setPhase('done');
        setUploadFeedback({
          type: 'error',
          message:
            result.extraction_usable === false
              ? t('dashboard.uploadDialog.extractionUnusable')
              : t('dashboard.retryFailed'),
        });
        return;
      }

      handleUploadSuccess({
        resumeId: resumeIdToRetry,
        fileId: fileIdToRemove,
        message: t('dashboard.retrySuccess'),
      });
    } catch (err) {
      console.error('Retry processing failed:', err);
      setPhase('done');
      setUploadFeedback({ type: 'error', message: t('dashboard.retryFailed') });
    } finally {
      setIsRetryingProcessing(false);
    }
  };

  const showProgress =
    isUploadingGlobal || isRetryingProcessing || (phase !== 'idle' && phase !== 'done');

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        {trigger || (
          <Button className="rounded-none border border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,0.1)] hover:translate-y-[1px] hover:translate-x-[1px] hover:shadow-none transition-all">
            <UploadIcon className="w-4 h-4 mr-2" />
            {t('dashboard.uploadResume')}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md bg-[#F0F0E8] border border-black shadow-[8px_8px_0px_0px_rgba(0,0,0,0.2)] p-0 gap-0 rounded-none">
        <DialogHeader className="p-6 border-b border-black bg-white">
          <DialogTitle className="font-serif text-2xl font-bold uppercase tracking-tight">
            {t('dashboard.uploadResume')}
          </DialogTitle>
        </DialogHeader>

        <div className="p-6 bg-[#F0F0E8]">
          <div
            className={`
                            relative border-2 border-dashed p-8 text-center transition-all duration-200
                            ${isDragging ? 'border-blue-700 bg-blue-50' : 'border-gray-400 hover:border-black hover:bg-white'}
                            ${currentFile ? 'bg-white border-solid border-black' : ''}
                            ${!currentFile && !isRetryingProcessing ? 'cursor-pointer' : 'cursor-default'}
                            ${isRetryingProcessing ? 'opacity-70' : ''}
                        `}
            onClick={!currentFile && !isRetryingProcessing ? openFileDialog : undefined}
            onDragEnter={isRetryingProcessing ? preventDropzoneInteraction : handleDragEnter}
            onDragLeave={isRetryingProcessing ? preventDropzoneInteraction : handleDragLeave}
            onDragOver={isRetryingProcessing ? preventDropzoneInteraction : handleDragOver}
            onDrop={isRetryingProcessing ? preventDropzoneInteraction : handleDrop}
          >
            <input {...getInputProps()} />

            {showProgress ? (
              <div className="flex flex-col items-center py-2" data-testid="upload-progress">
                <Loader2Icon className="w-10 h-10 animate-spin text-blue-700 mb-4" />
                <ol className="w-full max-w-xs text-left space-y-2 font-mono text-xs uppercase">
                  <ProgressStep
                    active={phase === 'uploading'}
                    done={phase === 'extracting' || phase === 'ai' || phase === 'done'}
                    label={t('dashboard.uploadDialog.phaseUpload')}
                  />
                  <ProgressStep
                    active={phase === 'extracting'}
                    done={phase === 'ai' || phase === 'done'}
                    label={t('dashboard.uploadDialog.phaseExtract')}
                  />
                  <ProgressStep
                    active={phase === 'ai' || isRetryingProcessing}
                    done={
                      phase === 'done' &&
                      lastDiagnostics?.ai_normalization_status === 'succeeded'
                    }
                    label={t('dashboard.uploadDialog.phaseAi')}
                  />
                </ol>
              </div>
            ) : currentFile ? (
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3 text-left overflow-hidden">
                  <div className="w-10 h-10 border border-black bg-gray-100 flex items-center justify-center shrink-0">
                    <FileIcon className="w-5 h-5 text-black" />
                  </div>
                  <div className="min-w-0">
                    <p className="font-bold text-sm truncate max-w-[200px]">
                      {currentFile.file.name}
                    </p>
                    <p className="font-mono text-xs text-gray-500">
                      {formatBytes(currentFile.file.size)}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={isRetryingProcessing}
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(currentFile.id);
                  }}
                  className="hover:bg-red-100 text-red-600 rounded-none"
                >
                  <XIcon className="w-5 h-5" />
                </Button>
              </div>
            ) : (
              <div className="flex flex-col items-center py-4">
                <div className="w-12 h-12 border border-black bg-white shadow-[4px_4px_0px_0px_rgba(0,0,0,0.1)] flex items-center justify-center mb-4">
                  <UploadIcon className="w-6 h-6 text-black" />
                </div>
                <p className="font-bold text-lg mb-1">
                  {t('dashboard.uploadDialog.dropzoneTitle')}
                </p>
                <p className="font-mono text-xs text-gray-500 uppercase">
                  {t('dashboard.uploadDialog.dropzoneSubtitle')}
                </p>
              </div>
            )}
          </div>

          {displayErrors.length > 0 && (
            <div
              className="mt-4 p-3 bg-red-50 border border-red-200 flex items-start gap-2 text-red-700 text-sm"
              data-testid="upload-error"
            >
              <AlertCircleIcon className="w-5 h-5 shrink-0" />
              <div>
                {displayErrors.map((err, i) => (
                  <p key={i}>{err}</p>
                ))}
              </div>
            </div>
          )}

          {uploadFeedback?.type === 'error' && lastDiagnostics && (
            <div
              className="mt-3 p-3 bg-white border border-black font-mono text-xs space-y-1"
              data-testid="upload-extract-fallback"
            >
              <p className="font-bold uppercase">{t('dashboard.uploadDialog.extractKeptTitle')}</p>
              {typeof lastDiagnostics.char_count === 'number' && (
                <p>
                  {t('dashboard.uploadDialog.charCount', {
                    count: lastDiagnostics.char_count,
                  })}
                </p>
              )}
              {lastDiagnostics.section_hints && lastDiagnostics.section_hints.length > 0 && (
                <p>
                  {t('dashboard.uploadDialog.sectionHints', {
                    hints: lastDiagnostics.section_hints.join(', '),
                  })}
                </p>
              )}
              {lastDiagnostics.ocr_needed && (
                <p>{t('dashboard.uploadDialog.ocrNeeded')}</p>
              )}
              {lastDiagnostics.reason_code && (
                <p className="text-gray-500">
                  {t('dashboard.uploadDialog.reasonCode', {
                    code: lastDiagnostics.reason_code,
                  })}
                </p>
              )}
              <p className="text-gray-600">{t('dashboard.uploadDialog.retryKeepsExtract')}</p>
            </div>
          )}

          {uploadFeedback?.type === 'success' && (
            <div className="mt-4 p-3 bg-green-50 border border-green-200 flex items-center gap-2 text-green-700 text-sm font-bold">
              <CheckCircle2Icon className="w-5 h-5 shrink-0" />
              <p>{uploadFeedback.message}</p>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-black bg-white flex justify-end gap-2">
          {uploadFeedback?.type === 'error' && failedResumeId && (
            <Button
              variant="outline"
              className="rounded-none border-black hover:bg-gray-100"
              onClick={handleRetryProcessing}
              disabled={isRetryingProcessing}
              data-testid="upload-retry-processing"
            >
              {isRetryingProcessing
                ? t('dashboard.retryingProcessing')
                : t('dashboard.retryProcessing')}
            </Button>
          )}
          {uploadFeedback?.type === 'error' && files.length > 0 && (
            <Button
              variant="outline"
              className="rounded-none border-black hover:bg-gray-100"
              disabled={isRetryingProcessing}
              onClick={() => {
                if (files[0]) removeFile(files[0].id);
                setUploadFeedback(null);
                setFailedResumeId(null);
                setLastDiagnostics(null);
                setPhase('idle');
              }}
            >
              {t('dashboard.uploadDialog.tryDifferentFile')}
            </Button>
          )}
          <DialogClose asChild>
            <Button variant="outline" className="rounded-none border-black hover:bg-gray-100">
              {t('common.cancel')}
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ProgressStep({
  active,
  done,
  label,
}: {
  active: boolean;
  done: boolean;
  label: string;
}) {
  return (
    <li
      className={`flex items-center gap-2 ${
        active ? 'text-blue-700 font-bold' : done ? 'text-green-700' : 'text-gray-400'
      }`}
    >
      <span
        className={`w-2 h-2 rounded-full border border-current ${active ? 'bg-blue-700' : done ? 'bg-green-700' : 'bg-transparent'}`}
        aria-hidden
      />
      {label}
    </li>
  );
}
