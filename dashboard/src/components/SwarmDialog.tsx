import { useEffect, useRef, type ReactNode } from "react";

export function SwarmDialog({
  title,
  busy = false,
  onClose,
  children,
}: {
  title: string;
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    close.current?.focus();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="swarm-dialog"
      aria-labelledby="swarm-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="swarm-heading">
        <h2 id="swarm-dialog-title">{title}</h2>
        <button
          ref={close}
          className="secondary-button"
          type="button"
          disabled={busy}
          onClick={onClose}
        >
          Close
        </button>
      </div>
      {children}
    </dialog>
  );
}
