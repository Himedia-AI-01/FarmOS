import { useEffect, useRef, useState } from 'react';
import { MdMic, MdStop } from 'react-icons/md';

// 브라우저 MediaRecorder 로 녹음 → 정지 시 Blob 을 콜백으로 전달.
// 권한 거부·미지원 환경에서는 버튼이 자동으로 비활성화된다.
// SSR 친화: window 접근은 useEffect 안에서만.

export function MicButton({
  onRecorded,
  disabled,
}: {
  onRecorded: (blob: Blob) => void;
  disabled?: boolean;
}) {
  const [supported, setSupported] = useState(false);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    setSupported(
      typeof window !== 'undefined' &&
        typeof window.MediaRecorder !== 'undefined' &&
        Boolean(navigator.mediaDevices?.getUserMedia),
    );
  }, []);

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        onRecorded(blob);
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      };
      recorder.start();
      setRecording(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : '마이크 접근 실패';
      setError(message);
    }
  };

  const stop = () => {
    recorderRef.current?.stop();
    setRecording(false);
  };

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (!supported) return null;

  return (
    <button
      type="button"
      onClick={recording ? stop : start}
      disabled={disabled}
      title={recording ? '녹음 종료' : '음성 입력'}
      aria-label={recording ? '녹음 종료' : '음성 입력'}
      className={`flex h-11 w-11 items-center justify-center rounded-lg border transition disabled:opacity-50 ${
        recording
          ? 'animate-pulse border-red-300 bg-red-50 text-red-600'
          : 'border-gray-300 bg-white text-gray-600 hover:border-primary/40 hover:text-primary'
      }`}
    >
      {recording ? <MdStop className="text-xl" /> : <MdMic className="text-xl" />}
      {error && <span className="sr-only">{error}</span>}
    </button>
  );
}
