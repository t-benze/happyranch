// Mirror MAX_ARTIFACT_BYTES and MAX_TASK_ATTACHMENT_BYTES in the daemon stores.
export const MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024;

/** WebKitGTK's scheme handler crashes on Blob-backed FormData. Encode inside
 * JavaScript so only bytes cross that boundary. The platform encoder owns CRLF,
 * name/filename escaping, MIME types and the random multipart boundary.
 * This buffers a multipart-sized copy in addition to the File/encoder buffers;
 * check file sizes before reading. Ordinary browser uploads stay unchanged. */
export async function prepareMultipartUpload(
  form: FormData,
  tooLarge: Error,
): Promise<{
  body: FormData | Uint8Array<ArrayBuffer>;
  headers: Record<string, string>;
}> {
  if (location.protocol !== 'happyranch-app:') {
    return { body: form, headers: {} };
  }
  for (const value of form.values()) {
    if (typeof value !== 'string' && value.size > MAX_UPLOAD_FILE_BYTES) {
      throw tooLarge;
    }
  }
  const encoded = new Response(form);
  return {
    body: new Uint8Array(await encoded.arrayBuffer()),
    headers: { 'Content-Type': encoded.headers.get('Content-Type')! },
  };
}
