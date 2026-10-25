export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export function uploadFileError(file: Pick<File, 'size'>): string | null {
  if (file.size === 0) return 'The selected file is empty. Choose a document containing text.';
  if (file.size > MAX_FILE_BYTES) return 'The selected file exceeds the 20 MB upload limit.';
  return null;
}
