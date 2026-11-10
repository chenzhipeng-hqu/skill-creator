# Embedded Shell Usage

Read this reference only when creating or editing embedded Shell help/usage text in self-authored C files. It does not apply to general Shell tasks or ordinary C logic edits.

## Source of truth

Before changing help, inspect the actual command parser/dispatcher for argument counts, accepted tokens, optional-argument grouping, defaults, aliases, and validation limits. The implementation is authoritative; do not infer or invent grammar from old help text.

## Writing convention

For several subcommands, use a standalone `usage:` heading and one complete command per displayed line:

```c
shell_write("usage:\r\n");
shell_write("  discharge route <on|off>\r\n");
shell_write("  discharge run <A> [thresholdV holdMs timeoutMs]\r\n");
shell_write("  discharge status\r\n");
shell_write("  discharge stop\r\n");
```

- Indent each command by two spaces and include the root command, so each line shows a complete invocation with argument placeholders.
- Use one `shell_write()` call per displayed line in multi-command blocks.
- Use `<...>` for required arguments, `[...]` for optional arguments, and `<a|b>` for choices. Group optional arguments according to the actual parser.
- Retain the project's `\r\n` ending in emitted help text.
- A focused single-command error may use one line, such as `usage: engine log <1-5>`; a multi-line block is unnecessary when it adds no clarity.
- Describe syntax only. Do not present a multi-command procedure as one invocation or imply execution or verified hardware state.

## Boundaries

Change only the requested help text, not command names, parsing, registration, or unrelated runtime output. Do not sweep existing help blocks. Vendor, generated, third-party, and externally maintained files remain excluded unless the exact file is explicitly brought under local ownership.

This is advisory writing guidance, not an automatic string rewriter, clang-format rule, Shell parser, lint/check mode, or bulk migration requirement. Existing package validation and regression tests do not prove that help matches its parser.
