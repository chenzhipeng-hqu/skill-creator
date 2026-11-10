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

/* Define to prevent recursive inclusion */
#ifndef @GUARD@
#define @GUARD@

#ifdef __cplusplus
extern "C" {
#endif

/***********************************************
                    include
***********************************************/
#include <stdbool.h>
#include <stdint.h>

/***********************************************
                    define
***********************************************/

/***********************************************
                    typedef
***********************************************/
enum {
    @MACRO@_0 = 0,
    @MACRO@_NUM,
};

enum {
    @MACRO@_CMD_NONE = 0,
    @MACRO@_CMD_XXX,
    @MACRO@_CMD_MAX,
};

typedef struct _@SYMBOL@Pub @SYMBOL@Pub_t;
typedef struct _@SYMBOL@CB @SYMBOL@CB_t;

struct _@SYMBOL@Pub {
    uintptr_t user_data;
};

struct _@SYMBOL@CB {
    int (*start)(@SYMBOL@Pub_t *pub);
    int (*done)(@SYMBOL@Pub_t *pub);
};

/***********************************************
               function prototypes
***********************************************/
/**
  * @brief
  * @param
  * @retval
  */
@SYMBOL@Pub_t *@SYMBOL@_getPub(uint8_t id);

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_setCallback(@SYMBOL@Pub_t *pub, const @SYMBOL@CB_t *cb);

/**
  * @brief   Report occupancy over the whole command transaction, including unconsumed results.
  * @param
  * @retval  <0: error; ==0: idle; ==1: busy
  */
int @SYMBOL@_status(@SYMBOL@Pub_t *pub);

/**
  * @brief   Submit a command and reserve the instance for owner until release.
  * @param   owner Non-NULL caller identity, e.g. the caller's state-machine address.
  * @retval  <0: error; ==0: ok; ==1: busy
  */
int @SYMBOL@_cmd(@SYMBOL@Pub_t *pub, const void *owner, uint8_t cmd, const void *data);

/**
  * @brief   Consume the result as a borrowed pointer; valid until the next accepted cmd() or init().
  * @param   owner Non-NULL caller identity used by the accepted cmd().
  * @retval  <0: error; ==0: busy; >0: resultLen
  */
int32_t @SYMBOL@_resultPoint(@SYMBOL@Pub_t *pub, const void *owner,
                           const void **ppRet, uint32_t *pRetLen);

/**
  * @brief   Copy and consume the result without releasing it on buffer errors.
  * @param   owner Non-NULL caller identity used by the accepted cmd().
  * @retval  <0: error; ==0: busy; >0: resultLen
  */
int32_t @SYMBOL@_result(@SYMBOL@Pub_t *pub, const void *owner, void *pRet, uint32_t retSize);

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_init(void);

/**
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_routine(void);

/***********************************************
                    inline
***********************************************/

/***********************************************
                   variable
***********************************************/

#ifdef __cplusplus
}
#endif

#endif /* @GUARD@ */

/**
  * @}
  */

/*************** (C) COPYRIGHT @YEAR@ *******END OF FILE******/
