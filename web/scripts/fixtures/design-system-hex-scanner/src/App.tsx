export function App() {
  const style = { color: '#aabbcc' }
  return (
    <main className="bg-[#123456] text-[#abc]">
      {/* GitHub issue #123 and PR #abcdef are references, not colors. */}
      <p>&#123; &#xabc; &#XABC;</p>
      <svg fill="#010203" />
      <span style={style}>fixture</span>
    </main>
  )
}
