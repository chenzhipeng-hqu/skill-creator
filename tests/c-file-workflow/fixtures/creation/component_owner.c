#include <assert.h>

#include "ticks.h"
#include "uart.c"

static uint32_t now;

uint32_t getTicks(void)
{
    return now;
}

uint32_t getTicksFrom(uint32_t tick)
{
    return now - tick;
}

static void assert_idle(uartPub_t *pub)
{
    uart_t *me = (uart_t *)pub;

    assert(uart_status(pub) == 0);
    assert(me->owner == NULL);
    assert(me->cmd == UART_CMD_NONE);
    assert(me->flag.busy == 0);
    assert(me->resultLen == 0);
}

static void finish_command(uartPub_t *pub, uint32_t value)
{
    uart_t *me = (uart_t *)pub;

    assert(uart_routine() == 0);
    assert(me->state == ST_XXX);
    /* Simulate the payload produced by the module-specific command handler. */
    me->result[0] = value;
    me->resultLen = sizeof(value);
    assert(uart_routine() == 0);
    assert(me->state == ST_FINISH);
    assert(me->flag.busy == 0);
    assert(uart_status(pub) == 1);
}

int main(void)
{
    int         caller_a = 0;
    int         caller_b = 0;
    uint32_t    value    = 0;
    uint32_t    length   = UINT32_MAX;
    const void *point    = &caller_b;
    uartPub_t  *pub      = uart_getPub(UART_0);
    uart_t     *me       = (uart_t *)pub;

    assert(pub != NULL);
    assert(uart_init() == 0);
    assert_idle(pub);

    assert(uart_cmd(NULL, &caller_a, UART_CMD_XXX, NULL) < 0);
    assert(uart_cmd(pub, NULL, UART_CMD_XXX, NULL) < 0);
    assert(uart_cmd(pub, &caller_a, UART_CMD_NONE, NULL) < 0);
    assert(uart_cmd(pub, &caller_a, UART_CMD_MAX, NULL) < 0);
    assert(uart_result(pub, NULL, &value, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, NULL, &point, &length) < 0);
    assert(uart_result(pub, &caller_a, &value, sizeof(value)) < 0);
    assert_idle(pub);

    assert(uart_cmd(pub, &caller_a, UART_CMD_XXX, NULL) == 0);
    assert(me->owner == &caller_a);
    assert(me->flag.busy == 1);
    assert(uart_status(pub) == 1);
    assert(uart_cmd(pub, &caller_a, UART_CMD_XXX, NULL) == 1);
    assert(uart_cmd(pub, &caller_b, UART_CMD_XXX, NULL) == 1);
    assert(uart_cmd(pub, &caller_b, UART_CMD_NONE, NULL) < 0);
    assert(uart_result(pub, &caller_a, &value, sizeof(value)) == 0);
    assert(uart_resultPoint(pub, &caller_a, &point, &length) == 0);
    assert(uart_result(pub, &caller_b, &value, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, &caller_b, &point, &length) < 0);
    assert(point == &caller_b && length == UINT32_MAX && value == 0);
    assert(me->owner == &caller_a && me->cmd == UART_CMD_XXX && me->flag.busy == 1);

    finish_command(pub, 42);
    assert(me->owner == &caller_a);
    assert(uart_cmd(pub, &caller_b, UART_CMD_XXX, NULL) == 1);
    assert(uart_result(pub, &caller_b, &value, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, &caller_b, &point, &length) < 0);
    assert(point == &caller_b && length == UINT32_MAX && value == 0);
    assert(uart_result(pub, &caller_a, &value, sizeof(value) - 1) < 0);
    assert(uart_result(pub, &caller_a, NULL, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, &caller_a, NULL, &length) < 0);
    assert(uart_resultPoint(pub, &caller_a, &point, NULL) < 0);
    assert(me->owner == &caller_a && me->state == ST_FINISH && me->resultLen == sizeof(value));
    assert(uart_result(pub, &caller_a, &value, sizeof(value)) == (int32_t)sizeof(value));
    assert(value == 42 && me->owner == NULL && me->cmd == UART_CMD_NONE);
    assert(uart_resultPoint(pub, &caller_a, &point, &length) < 0);
    assert(uart_routine() == 0);
    assert_idle(pub);

    assert(uart_cmd(pub, &caller_b, UART_CMD_XXX, NULL) == 0);
    finish_command(pub, 84);
    assert(uart_resultPoint(pub, &caller_b, &point, &length) == (int32_t)sizeof(value));
    assert(point == me->result && length == sizeof(value));
    assert(*(const uint32_t *)point == 84);
    assert(me->owner == NULL && me->cmd == UART_CMD_NONE);
    assert(uart_result(pub, &caller_b, &value, sizeof(value)) < 0);
    assert(uart_routine() == 0);
    assert_idle(pub);

    /* A's expired transaction must not let A poll or consume B's new result. */
    assert(uart_cmd(pub, &caller_a, UART_CMD_XXX, NULL) == 0);
    finish_command(pub, 21);
    now += 1001;
    assert(uart_routine() == 0);
    assert_idle(pub);
    assert(uart_cmd(pub, &caller_b, UART_CMD_XXX, NULL) == 0);
    assert(uart_result(pub, &caller_a, &value, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, &caller_a, &point, &length) < 0);
    assert(me->owner == &caller_b && me->flag.busy == 1);
    finish_command(pub, 99);
    assert(uart_result(pub, &caller_a, &value, sizeof(value)) < 0);
    assert(uart_resultPoint(pub, &caller_a, &point, &length) < 0);
    assert(value == 42 && length == sizeof(value) && point == me->result);
    assert(me->owner == &caller_b && me->state == ST_FINISH && me->result[0] == 99);
    assert(uart_result(pub, &caller_b, &value, sizeof(value)) == (int32_t)sizeof(value));
    assert(value == 99);
    assert(uart_routine() == 0);
    assert_idle(pub);

    assert(uart_cmd(pub, &caller_a, UART_CMD_XXX, NULL) == 0);
    me->state = ST_INIT;
    now += 2001;
    assert(uart_routine() == 0);
    assert(me->state == ST_TIMEOUT);
    assert(uart_routine() == 0);
    assert_idle(pub);

    assert(uart_cmd(pub, &caller_b, UART_CMD_XXX, NULL) == 0);
    me->state = ST_ERROR;
    assert(uart_routine() == 0);
    assert_idle(pub);

    assert(uart_cmd(pub, &caller_a, UART_CMD_XXX, NULL) == 0);
    assert(uart_init() == 0);
    assert_idle(pub);
    return 0;
}
