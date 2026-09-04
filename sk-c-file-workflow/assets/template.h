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
  * @brief
  * @param
  * @retval
  */
int @SYMBOL@_status(@SYMBOL@Pub_t *pub);

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
