// Bare-metal IRQ setup; lwIP timers use the architectural counter in monitor.c.
#include "xparameters.h"
#include "xil_exception.h"
#include "xil_cache.h"
#include "xscugic.h"
#include "xil_io.h"
#include "xuartps.h"

volatile int TcpFastTmrFlag, TcpSlowTmrFlag;

void init_platform(void) {
    static XUartPs uart;
    // Verified MYIR UART0 on MIO34/35; live IOPLL is 1.5 GHz.
    Xil_Out32(0xff180088, (Xil_In32(0xff180088) & ~0xfeU) | 0xc0U);
    Xil_Out32(0xff18008c, (Xil_In32(0xff18008c) & ~0xfeU) | 0xc0U);
    Xil_Out32(0xff180208, Xil_In32(0xff180208) & ~(1U << 3));
    Xil_Out32(0xff5e0074, 0x01010f00U);
    XUartPs_Config *config = XUartPs_LookupConfig(XPAR_XUARTPS_0_DEVICE_ID);
    XUartPs_CfgInitialize(&uart, config, config->BaseAddress);
    XUartPs_SetBaudRate(&uart, 115200);
    // Preserve the observed working board reset bias: MIO40 is high impedance.
    Xil_Out32(0xff0a0248, Xil_In32(0xff0a0248) & ~(1U << 14));
    Xil_Out32(0xff0a0244, Xil_In32(0xff0a0244) & ~(1U << 14));
    Xil_ExceptionInit();
    XScuGic_DeviceInitialize(XPAR_SCUGIC_SINGLE_DEVICE_ID);
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_IRQ_INT,
        (Xil_ExceptionHandler)XScuGic_DeviceInterruptHandler,
        (void *)XPAR_SCUGIC_SINGLE_DEVICE_ID);
}

void platform_enable_interrupts(void) {Xil_ExceptionEnableMask(XIL_EXCEPTION_IRQ);}
void cleanup_platform(void) {Xil_ICacheDisable(); Xil_DCacheDisable();}
