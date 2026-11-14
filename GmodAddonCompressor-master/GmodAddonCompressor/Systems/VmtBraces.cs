namespace GmodAddonCompressor.Systems
{
    internal static class VmtBraces
    {
        /// <summary>Blocks opened and not closed, counting only braces that are KeyValues syntax: a brace inside
        /// a quoted string ("halflife/{grate2": GoldSrc-style alpha-tested texture names start with "{") or in a
        /// // comment is text. Counting every character made VmtSyntaxRepairService append a "}" to such valid
        /// VMTs, and the game then printed "KeyValues Error: LoadFromBuffer: missing {" (ttt_rooftops_2016_v1,
        /// 2026-11-14).</summary>
        internal static int UnclosedBlocks(string content)
        {
            int depth = 0;
            bool quoted = false;
            for (int i = 0; i < content.Length; i++)
            {
                char ch = content[i];
                if (quoted)
                {
                    // an unterminated string ends with its line, as the game reads it
                    if (ch == '"' || ch == '\n')
                        quoted = false;
                    continue;
                }
                if (ch == '"')
                    quoted = true;
                else if (ch == '/' && i + 1 < content.Length && content[i + 1] == '/')
                {
                    int end = content.IndexOf('\n', i);
                    if (end < 0)
                        break;
                    i = end;
                }
                else if (ch == '{')
                    depth++;
                else if (ch == '}')
                    depth--;
            }
            return depth;
        }
    }
}
