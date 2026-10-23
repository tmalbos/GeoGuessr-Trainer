/** URL of one of a clue's asset files. `rev` (the file's mtime, sent by the list endpoint) is a
 *  cache-buster: files can be overwritten in place under the same name, so without ?v= the browser
 *  would keep showing the old image until a manual reload. */
export default function fileUrl(cc, name, rev) {
  return rev != null ? `/api/clues/${cc}/files/${name}?v=${rev}` : `/api/clues/${cc}/files/${name}`;
}
