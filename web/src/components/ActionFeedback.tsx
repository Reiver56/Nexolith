export interface Feedback {
  tone: "success" | "warning";
  message: string;
}

export function ActionFeedback({ feedback }: { feedback: Feedback | null }) {
  if (feedback === null) {
    return null;
  }
  return (
    <p
      className={`action-feedback action-feedback--${feedback.tone}`}
      role="status"
      aria-live="polite"
    >
      {feedback.message}
    </p>
  );
}
