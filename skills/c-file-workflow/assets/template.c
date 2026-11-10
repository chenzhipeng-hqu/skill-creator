/**
 ******************************************************************************
 * @file    @FILE@
 * @author  @AUTHOR@
 * @version V1.0.0
 * @date    @DATE@
 * @brief
 ******************************************************************************
 * @attention
 ******************************************************************************
 */

/** @addtogroup Project
  * @{
  */

/***********************************************
                    include
***********************************************/
#include <string.h>
#include <stddef.h>

/* Find the target project's tick and elapsed-time APIs and include their header here.
 * Do not assume the header is named systick.h. Adapt getTicks()/getTicksFrom() below
 * to the actual APIs and match timeout values to their time units.
 */

#include "@HEADER@"

/***********************************************
                    define
***********************************************/
#define  RESULT_MAX   4    // define at every module

/***********************************************
                    typedef
***********************************************/
enum {
    ST_IDLE = 0,
    ST_INIT,
    /* Add more states here when needed. */
    ST_XXX,
    ST_END,
    ST_FINISH,
    ST_TIMEOUT,
    ST_ERROR,
    ST_MAX,
};

typedef struct _@SYMBOL@Ops @SYMBOL@Ops_t;

typedef struct _@SYMBOL@ {
    @SYMBOL@Pub_t pub; /* pub must be first */
    const @SYMBOL@CB_t *cb;
    const @SYMBOL@Ops_t *ops;
    const void *owner; /* Caller identity; NULL means unowned. */
    struct {
        uint32_t start : 1;
        uint32_t busy : 1;   // in cmd execute, busy=1
        uint32_t debug : 1;
        uint32_t reserved : 28;
    } flag;
    uint32_t tick;
    uint32_t @SYMBOL@Tick;
    uint8_t state;
    uint8_t id;
    uint8_t cmd;
    uint8_t loglvl;
    uint32_t resultLen;    // result byte count
    uint32_t result[RESULT_MAX];
} @SYMBOL@_t;

// Hardware-specific operations belong in the Ops table.
struct _@SYMBOL@Ops {
    int (*init)(@SYMBOL@_t *me);
};

typedef int (*handler_t)(@SYMBOL@_t *me);

/***********************************************
              function prototypes
***********************************************/

/***********************************************
                   variable
***********************************************/
static @SYMBOL@_t @PLURAL@[@MACRO@_NUM];

/***********************************************
                   function
***********************************************/
/*********************************** ops function ************************************/
static int @SYMBOL@Ops_init(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static const @SYMBOL@Ops_t @SYMBOL@_ops = {
    .init = @SYMBOL@Ops_init,
};

/********************************** static function **********************************/
// common state handler
static int st_common(@SYMBOL@_t *me)
{
    (void)me;

    return 0;
}

static int st_idle(@SYMBOL@_t *me)
{
    (void)me;

    switch (me->cmd) {
    case @MACRO@_CMD_XXX:
        me->state = ST_XXX;

        break;
    default:
        break;
    }

    return 0;
}

static int st_init(@SYMBOL@_t *me)
{
    (void)me;

    // timeout for example
    if (getTicksFrom(me->tick) > 2000) {
        me->state = ST_TIMEOUT;
    }

    return 0;
}

static int st_xxx(@SYMBOL@_t *me)
{
    // TODO: for example，check resultLen > sizeof(me->result)
    // TODO: set result and resultLen

    me->tick = getTicks();
    me->flag.busy = 0;
    me->state = ST_FINISH;

    return 0;
}

static int st_end(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_finish(@SYMBOL@_t *me)
{
    // every result timeout less than 1000ms
    if (getTicksFrom(me->tick) > 1000) {
        // TODO: add log warning
        me->cmd = @MACRO@_CMD_NONE;
    }

    if (me->cmd == @MACRO@_CMD_NONE) {
        me->owner = NULL;
        me->resultLen = 0;
        me->state = ST_IDLE;
    }

    return 0;
}

static int st_timeout(@SYMBOL@_t *me)
{
    me->owner = NULL;
    me->flag.busy = 0;
    me->resultLen = 0;
    me->cmd = @MACRO@_CMD_NONE;
    me->state = ST_IDLE;

    return 0;
}

static int st_error(@SYMBOL@_t *me)
{
    // TODO：maybe need reinit
    me->owner = NULL;
    me->flag.busy = 0;
    me->resultLen = 0;
    me->cmd = @MACRO@_CMD_NONE;
    me->state = ST_IDLE;

    return 0;
}

static const handler_t @SYMBOL@_handler[ST_MAX] = {
    st_idle,
    st_init,
    st_xxx,
    st_end,
    st_finish,
    st_timeout,
    st_error,
};

/********************************** public function **********************************/
/**
  * @brief
  * @param
  * @retval
  */
@SYMBOL@Pub_t *@SYMBOL@_getPub(uint8_t id)
{
    if (id >= @MACRO@_NUM) {
        return NULL;
    }

    return &@PLURAL@[id].pub;
}

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_setCallback(@SYMBOL@Pub_t *pub, const @SYMBOL@CB_t *cb)
{
    @SYMBOL@_t *me;

    if (pub == NULL) {
        return -1;
    }

    me = (@SYMBOL@_t *)pub;
    me->cb = cb;
    return 0;
}

/**
  * @brief   Report occupancy over the whole command transaction, including unconsumed results.
  * @param
  * @retval  <0: error; ==0: idle; ==1: busy
  */
int @SYMBOL@_status(@SYMBOL@Pub_t *pub)
{
    @SYMBOL@_t *me;

    if (pub == NULL) {
        return -1;
    }

    me = (@SYMBOL@_t *)pub;
    if ((me->owner != NULL) || (me->cmd != @MACRO@_CMD_NONE) || (me->state != ST_IDLE) ||
        (me->flag.busy == true)) {
        return 1;
    }

    return 0;
}

/**
  * @brief   Submit a command and reserve the instance for owner until release.
  * @param   owner Non-NULL caller identity, e.g. the caller's state-machine address.
  * @retval  <0: error; ==0: ok; ==1: busy
  */
int @SYMBOL@_cmd(@SYMBOL@Pub_t *pub, const void *owner, uint8_t cmd, const void *data)
{
    int ret;
    @SYMBOL@_t *me;
    (void)data;

    if (pub == NULL || owner == NULL) {
        return -1;
    }

    if ((cmd <= @MACRO@_CMD_NONE) || (cmd >= @MACRO@_CMD_MAX)) {
        return -2;
    }

    me = (@SYMBOL@_t *)pub;

    ret = @SYMBOL@_status(pub);
    if (ret) {
        return ret;
    }

    me->owner = owner;
    me->resultLen = 0;
    me->flag.busy = 1;
    me->cmd = cmd;
    me->tick = getTicks();

    return 0;
}

/**
  * @brief   Consume the result as a borrowed pointer; valid until the next accepted cmd() or init().
  * @param   owner Non-NULL caller identity used by the accepted cmd().
  * @retval  <0: error; ==0: busy; >0: resultLen
  */
int32_t @SYMBOL@_resultPoint(@SYMBOL@Pub_t *pub, const void *owner,
                           const void **ppRet, uint32_t *pRetLen)
{
    @SYMBOL@_t *me;

    if (pub == NULL || owner == NULL) {
        return -1;
    }

    me = (@SYMBOL@_t *)pub;

    if (me->owner != owner) {
        return -2;
    }

    if (me->flag.busy) {
        return 0;
    }

    if (me->cmd <= @MACRO@_CMD_NONE) {
        return -3;
    }

    if (me->state != ST_FINISH) {
        return -4;
    }

    if (ppRet == NULL || pRetLen == NULL) {
        return -5;
    }

    if ((0 >= me->resultLen) || me->resultLen > sizeof(me->result)) {
        return -6;
    }

    // TODO: add result point
    *ppRet = me->result;
    *pRetLen = me->resultLen;

    me->owner = NULL;
    me->cmd = @MACRO@_CMD_NONE;

    return me->resultLen;
}

/**
  * @brief   Copy and consume the result without releasing it on buffer errors.
  * @param   owner Non-NULL caller identity used by the accepted cmd().
  * @retval  <0: error; ==0: busy; >0: resultLen
  */
int32_t @SYMBOL@_result(@SYMBOL@Pub_t *pub, const void *owner, void *pRet, uint32_t retSize)
{
    @SYMBOL@_t *me;

    if (pub == NULL || owner == NULL) {
        return -1;
    }

    me = (@SYMBOL@_t *)pub;

    if (me->owner != owner) {
        return -2;
    }

    if (me->flag.busy) {
        return 0;
    }

    if (me->cmd <= @MACRO@_CMD_NONE) {
        return -3;
    }

    if (me->state != ST_FINISH) {
        return -4;
    }

    if (pRet == NULL || retSize <= 0) {
        return -5;
    }

    if ((0 >= me->resultLen) || (me->resultLen > sizeof(me->result))) {
        return -6;
    }

    if (retSize < me->resultLen) {
        return -7;
    }

    // TODO: copy result for exapmle, decide on pRet type
    memcpy(pRet, me->result, me->resultLen);

    me->owner = NULL;
    me->cmd = @MACRO@_CMD_NONE;

    return me->resultLen;
}

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_init(void)
{
    @SYMBOL@_t *me;
    int ret;

    for (uint8_t id = 0; id < @MACRO@_NUM; id++) {
        me = &@PLURAL@[id];
        me->id = id;
        me->ops = &@SYMBOL@_ops;
        ret = me->ops->init(me);
        if (ret != 0) {
            return ret;
        }

        me->resultLen = 0;
        me->flag.busy = 0;
        me->owner = NULL;
        me->state = ST_IDLE;
        me->cmd = @MACRO@_CMD_NONE;
    }

    return 0;
}

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_routine(void)
{
    int ret = 0;
    static uint8_t id = 0;
    @SYMBOL@_t *me;

    if ((++id) >= @MACRO@_NUM) {
        id = 0;
    }

    me = &@PLURAL@[id];
    if (me->state >= ST_MAX) {
        me->state = ST_IDLE;
    }

    st_common(me);

    ret = @SYMBOL@_handler[me->state](me);

    return ret;
}

/**
  * @}
  */

/*************** (C) COPYRIGHT @YEAR@ *******END OF FILE******/
