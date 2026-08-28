using System.Text;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal readonly record struct VmtSourceSpan(
    int Offset,
    int Length,
    int Line,
    int Column);

internal sealed record VmtComment(string Text, VmtSourceSpan Span);

internal sealed record VmtKeyValuesValue(
    string Text,
    string RawText,
    bool WasQuoted,
    VmtSourceSpan Span);

internal sealed record VmtKeyValuesNode(
    string Key,
    bool KeyWasQuoted,
    VmtSourceSpan KeySpan,
    VmtKeyValuesValue? Value,
    IReadOnlyList<VmtKeyValuesNode> Children,
    string? Condition,
    VmtSourceSpan Span)
{
    internal bool IsBlock => Value is null;
}

internal sealed record VmtDocument(
    string SourcePath,
    string Shader,
    bool ShaderWasQuoted,
    IReadOnlyList<VmtKeyValuesNode> Entries,
    IReadOnlyList<VmtComment> Comments,
    VmtSourceSpan Span);

internal static class VmtKeyValuesParser
{
    internal static VmtDocument Parse(string content, string sourcePath)
    {
        ArgumentNullException.ThrowIfNull(content);
        if (string.IsNullOrWhiteSpace(sourcePath))
        {
            throw new ArgumentException("A VMT source path is required.", nameof(sourcePath));
        }

        var lexer = new Lexer(content, sourcePath);
        IReadOnlyList<Token> tokens = lexer.ReadAll();
        if (tokens.Count == 0)
        {
            throw Error(sourcePath, 1, 1, "empty document");
        }

        var parser = new Parser(tokens, sourcePath);
        Token shader = parser.RequireText("shader");
        Token opening = parser.Require(TokenKind.OpenBrace, "opening root brace");
        (IReadOnlyList<VmtKeyValuesNode> entries, Token closing) = parser.ReadBlock(opening);
        parser.RequireEnd();

        return new VmtDocument(
            sourcePath.Replace('\\', '/'),
            shader.Text,
            shader.WasQuoted,
            entries,
            lexer.Comments.AsReadOnly(),
            SpanFrom(shader, closing));
    }

    private static InvalidDataException Error(
        string sourcePath,
        int line,
        int column,
        string message) =>
        new($"vmt_parse:{sourcePath.Replace('\\', '/')}:{line}:{column}:{message}");

    private static VmtSourceSpan SpanFrom(Token first, Token last) =>
        new(
            first.Span.Offset,
            checked(last.Span.Offset + last.Span.Length - first.Span.Offset),
            first.Span.Line,
            first.Span.Column);

    private enum TokenKind
    {
        Text,
        OpenBrace,
        CloseBrace
    }

    private sealed record Token(
        TokenKind Kind,
        string Text,
        string RawText,
        bool WasQuoted,
        VmtSourceSpan Span);

    private sealed class Parser
    {
        private readonly IReadOnlyList<Token> _tokens;
        private readonly string _sourcePath;
        private int _index;

        internal Parser(IReadOnlyList<Token> tokens, string sourcePath)
        {
            _tokens = tokens;
            _sourcePath = sourcePath;
        }

        internal Token RequireText(string context)
        {
            Token token = Require(TokenKind.Text, context);
            if (IsCondition(token))
            {
                throw At(token, $"condition where {context} was expected");
            }

            return token;
        }

        internal Token Require(TokenKind kind, string context)
        {
            if (_index >= _tokens.Count)
            {
                Token last = _tokens[^1];
                throw Error(
                    _sourcePath,
                    last.Span.Line,
                    last.Span.Column + last.Span.Length,
                    $"missing {context}");
            }

            Token token = _tokens[_index++];
            if (token.Kind != kind)
            {
                throw At(token, $"expected {context}");
            }

            return token;
        }

        internal (IReadOnlyList<VmtKeyValuesNode> Entries, Token Closing) ReadBlock(
            Token opening)
        {
            var entries = new List<VmtKeyValuesNode>();
            while (_index < _tokens.Count && _tokens[_index].Kind != TokenKind.CloseBrace)
            {
                Token key = RequireText("key");
                if (_index >= _tokens.Count)
                {
                    throw At(key, "key has no value or block");
                }

                Token next = _tokens[_index++];
                VmtKeyValuesValue? value;
                IReadOnlyList<VmtKeyValuesNode> children;
                Token end;
                if (next.Kind == TokenKind.OpenBrace)
                {
                    (children, end) = ReadBlock(next);
                    value = null;
                }
                else if (next.Kind == TokenKind.Text && !IsCondition(next))
                {
                    value = new VmtKeyValuesValue(
                        next.Text,
                        next.RawText,
                        next.WasQuoted,
                        next.Span);
                    children = Array.Empty<VmtKeyValuesNode>();
                    end = next;
                }
                else
                {
                    throw At(next, "expected value or opening brace");
                }

                string? condition = null;
                if (_index < _tokens.Count &&
                    _tokens[_index].Kind == TokenKind.Text &&
                    IsCondition(_tokens[_index]))
                {
                    end = _tokens[_index++];
                    condition = end.Text;
                }

                entries.Add(new VmtKeyValuesNode(
                    key.Text,
                    key.WasQuoted,
                    key.Span,
                    value,
                    children,
                    condition,
                    SpanFrom(key, end)));
            }

            Token closing = Require(TokenKind.CloseBrace, "closing brace");
            return (entries.AsReadOnly(), closing);
        }

        internal void RequireEnd()
        {
            if (_index < _tokens.Count)
            {
                throw At(_tokens[_index], "trailing token after root block");
            }
        }

        private InvalidDataException At(Token token, string message) =>
            Error(_sourcePath, token.Span.Line, token.Span.Column, message);

        private static bool IsCondition(Token token) =>
            !token.WasQuoted &&
            token.Text.Length >= 2 &&
            token.Text[0] == '[' &&
            token.Text[^1] == ']';
    }

    private sealed class Lexer
    {
        private readonly string _content;
        private readonly string _sourcePath;
        private readonly List<VmtComment> _comments = new();
        private int _offset;
        private int _line = 1;
        private int _column = 1;

        internal Lexer(string content, string sourcePath)
        {
            _content = content;
            _sourcePath = sourcePath;
        }

        internal List<VmtComment> Comments => _comments;

        internal IReadOnlyList<Token> ReadAll()
        {
            var tokens = new List<Token>();
            while (true)
            {
                SkipTrivia();
                if (_offset >= _content.Length)
                {
                    return tokens.AsReadOnly();
                }

                tokens.Add(ReadToken());
            }
        }

        private Token ReadToken()
        {
            int offset = _offset;
            int line = _line;
            int column = _column;
            char current = _content[_offset];
            if (current is '{' or '}')
            {
                Advance();
                return new Token(
                    current == '{' ? TokenKind.OpenBrace : TokenKind.CloseBrace,
                    current.ToString(),
                    current.ToString(),
                    WasQuoted: false,
                    new VmtSourceSpan(offset, 1, line, column));
            }

            if (current == '"')
            {
                return ReadQuoted(offset, line, column);
            }

            var value = new StringBuilder();
            while (_offset < _content.Length)
            {
                current = _content[_offset];
                if (char.IsWhiteSpace(current) || current is '{' or '}' or '"')
                {
                    break;
                }

                value.Append(current);
                Advance();
            }

            if (value.Length == 0)
            {
                throw Error(_sourcePath, line, column, $"unexpected character '{current}'");
            }

            string text = value.ToString();
            return new Token(
                TokenKind.Text,
                text,
                text,
                WasQuoted: false,
                new VmtSourceSpan(offset, _offset - offset, line, column));
        }

        private Token ReadQuoted(int offset, int line, int column)
        {
            Advance();
            var decoded = new StringBuilder();
            while (_offset < _content.Length)
            {
                char current = _content[_offset];
                if (current == '"')
                {
                    Advance();
                    string raw = _content.Substring(offset, _offset - offset);
                    return new Token(
                        TokenKind.Text,
                        decoded.ToString(),
                        raw,
                        WasQuoted: true,
                        new VmtSourceSpan(offset, _offset - offset, line, column));
                }

                if (current == '\\')
                {
                    Advance();
                    if (_offset >= _content.Length)
                    {
                        break;
                    }

                    char escaped = _content[_offset];
                    if (escaped is 'n' or 'r' or 't' or '"' or '\\')
                    {
                        decoded.Append(escaped switch
                        {
                            'n' => '\n',
                            'r' => '\r',
                            't' => '\t',
                            _ => escaped
                        });
                    }
                    else
                    {
                        decoded.Append('\\');
                        decoded.Append(escaped);
                    }
                    Advance();
                    continue;
                }

                decoded.Append(current);
                Advance();
            }

            throw Error(_sourcePath, line, column, "unterminated quoted string");
        }

        private void SkipTrivia()
        {
            while (_offset < _content.Length)
            {
                if (char.IsWhiteSpace(_content[_offset]))
                {
                    Advance();
                    continue;
                }

                if (StartsWith("//"))
                {
                    ReadLineComment();
                    continue;
                }

                if (StartsWith("/*"))
                {
                    ReadBlockComment();
                    continue;
                }

                return;
            }
        }

        private void ReadLineComment()
        {
            int offset = _offset;
            int line = _line;
            int column = _column;
            Advance();
            Advance();
            while (_offset < _content.Length && _content[_offset] is not '\r' and not '\n')
            {
                Advance();
            }

            _comments.Add(new VmtComment(
                _content.Substring(offset, _offset - offset),
                new VmtSourceSpan(offset, _offset - offset, line, column)));
        }

        private void ReadBlockComment()
        {
            int offset = _offset;
            int line = _line;
            int column = _column;
            Advance();
            Advance();
            while (_offset < _content.Length && !StartsWith("*/"))
            {
                Advance();
            }

            if (_offset >= _content.Length)
            {
                throw Error(_sourcePath, line, column, "unterminated block comment");
            }

            Advance();
            Advance();
            _comments.Add(new VmtComment(
                _content.Substring(offset, _offset - offset),
                new VmtSourceSpan(offset, _offset - offset, line, column)));
        }

        private bool StartsWith(string value) =>
            _offset + value.Length <= _content.Length &&
            _content.AsSpan(_offset, value.Length).SequenceEqual(value.AsSpan());

        private void Advance()
        {
            char current = _content[_offset++];
            if (current == '\n')
            {
                _line++;
                _column = 1;
            }
            else
            {
                _column++;
            }
        }
    }
}
