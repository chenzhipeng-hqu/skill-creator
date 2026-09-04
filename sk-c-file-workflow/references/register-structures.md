# Register Structures

Use these conventions for a documented external-chip register map regardless of whether its transport is SPI, I2C, UART, parallel, or another bus. A command-only device without a register map is exempt.

## Eight-bit registers

Represent an 8-bit register as a one-byte union with raw and named views:

```c
#define CHIP_CFG1_RESET 0x91U

typedef union _chipRegCfg1 {
    uint8_t value;
    struct {
        uint8_t data_rate : 3;  /* bit 2:0 */
        uint8_t reserved3 : 2;  /* bit 4:3 */
        uint8_t clock_en  : 1;  /* bit 5 */
        uint8_t daisy_in  : 1;  /* bit 6 */
        uint8_t reserved7 : 1;  /* bit 7 */
    } bit;
} chipRegCfg1_t;

_Static_assert(sizeof(chipRegCfg1_t) == 1U, "CFG1 register must be one byte");
```

- Declare fields from the least-significant bit to the most-significant bit for the selected target ABI.
- Preserve every reserved bit and document its fixed or read-back requirement from the datasheet.
- Define field enums as raw, unshifted field codes; assign them through the bit field.
- Initialize `.value` from the documented reset value before changing fields.
- Transfer one-byte registers through `.value`, and assign received bytes to `.value` before reading `.bit`.
- Reuse one union type for registers with an identical layout.

Bit-field order is implementation-defined. A size assertion is necessary but insufficient: add known-value layout tests using datasheet encodings and rerun them with the target toolchain whenever the compiler or ABI changes.

## Multi-byte registers

Represent multi-byte wire values as individual bytes or explicit encode/decode functions. Do not transmit a multi-byte C union or structure directly because byte order and padding are ABI-dependent.

```c
static uint16_t chip_decode_u16_be(const uint8_t bytes[2])
{
    return (uint16_t)(((uint16_t)bytes[0] << 8U) | bytes[1]);
}
```

State the device byte order from its documentation and add known-value tests. Host-only layout and encode/decode tests do not prove target or hardware behavior.

## Review boundary

The Python checker can verify deterministic source facts such as a `.value/.bit` union, total bit width, a one-byte size assertion, and absence of shifted enum codes. It cannot verify datasheet truth, reset values, reserved-bit meaning, bus order, or hardware behavior. Treat those as Agent review items backed by the device documentation.
