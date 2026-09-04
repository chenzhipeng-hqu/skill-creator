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
#include <stddef.h>

#include "@HEADER@"

/***********************************************
                    define
***********************************************/

/***********************************************
                    typedef
***********************************************/
enum {
    ST_IDLE = 0,
    ST_INIT,
    /* Add more states here when needed. */
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
    struct {
        uint32_t start : 1;
        uint32_t busy : 1;
        uint32_t reserved : 30;
    } flag;
    uint32_t tick;
    uint32_t @SYMBOL@Tick;
    uint8_t state;
    uint8_t id;
    uint8_t cmd;
    uint8_t loglvl;
} @SYMBOL@_t;

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
static int st_idle(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_init(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_end(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_finish(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_timeout(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static int st_error(@SYMBOL@_t *me)
{
    (void)me;
    return 0;
}

static const handler_t @SYMBOL@_handler[ST_MAX] = {
    st_idle,
    st_init,
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
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_status(@SYMBOL@Pub_t *pub)
{
    @SYMBOL@_t *me;

    if (pub == NULL) {
        return -1;
    }

    me = (@SYMBOL@_t *)pub;
    if ((me->cmd != @MACRO@_CMD_NONE) || (me->state != ST_IDLE) ||
        (me->flag.busy == true)) {
        return 1;
    }

    return 0;
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
    static uint8_t id = 0;
    @SYMBOL@_t *me;

    if ((++id) >= @MACRO@_NUM) {
        id = 0;
    }

    me = &@PLURAL@[id];
    if (me->state >= ST_MAX) {
        me->state = ST_IDLE;
    }

    return @SYMBOL@_handler[me->state](me);
}

/**
  * @}
  */

/*************** (C) COPYRIGHT @YEAR@ *******END OF FILE******/
