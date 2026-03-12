import { useRef, useState } from "react";
import { FileArchive, UploadCloud } from "lucide-react";

export const UploadZone = ({ file, onFileSelect }) => {
  const inputRef = useRef(null);
  const [isDragging, setIsDragging] = useState(false);

  const onDrop = (event) => {
    event.preventDefault();
    setIsDragging(false);
    const uploadedFile = event.dataTransfer.files?.[0];
    if (uploadedFile) {
      onFileSelect(uploadedFile);
    }
  };

  return (
    <div
      className={[
        "rounded-[1.25rem] border-2 border-dashed bg-[#fbfbfb] p-7",
        "transition-transform duration-200 ease-out",
        isDragging
          ? "scale-[1.01] border-[#eb6a45] bg-[#fff1eb]"
          : "border-slate-300 hover:border-[#eb6a45]",
      ].join(" ")}
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onDrop}
      data-testid="resume-zip-upload-zone"
    >
      <input
        ref={inputRef}
        type="file"
        accept=".zip"
        className="hidden"
        onChange={(event) => onFileSelect(event.target.files?.[0] || null)}
        data-testid="resume-zip-file-input"
      />

      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <p className="flex items-center gap-2 text-sm font-semibold text-slate-900" data-testid="upload-zone-title">
            <FileArchive size={16} /> Drop Resume ZIP Folder
          </p>
          <p className="text-sm text-slate-500" data-testid="upload-zone-description">
            Supports PDF/DOCX/TXT files inside one ZIP file.
          </p>
          {file ? (
            <p className="font-mono text-xs text-[#d7552f]" data-testid="selected-zip-file-name">
              Selected: {file.name}
            </p>
          ) : (
            <p className="font-mono text-xs text-slate-500" data-testid="selected-zip-file-placeholder">
              No ZIP selected yet
            </p>
          )}
        </div>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="inline-flex items-center gap-2 rounded-full border border-[#eb6a45] bg-[#eb6a45] px-5 py-2 text-sm font-medium text-white hover:-translate-y-0.5 hover:bg-[#d7552f]"
          data-testid="select-zip-button"
        >
          <UploadCloud size={16} /> Choose ZIP
        </button>
      </div>
    </div>
  );
};
