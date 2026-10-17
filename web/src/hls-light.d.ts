// hls.js ships no types for its light build (no subtitles, alternate audio, DRM or CMCD,
// none of which WiePlay uses); it has the same API as the full build.
declare module 'hls.js/light' {
  import Hls from 'hls.js'
  export default Hls
}
