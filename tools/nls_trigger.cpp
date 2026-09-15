// Newland NLS 扫码枪 软件触发控制（UDI 指令）
//
// 编译：
//   g++ -O2 -o nls_trigger nls_trigger.cpp -I include \
//       -L lib/amd64 -lnldevicemaster -Wl,-rpath,'$ORIGIN'
//
// 运行（需能访问扫码枪 USB，必要时 sudo）：
//   ./nls_trigger                                        # 默认：开始模拟触发扫描(SCNTRG1)
//   ./nls_trigger "7e 01 30 30 30 30 23 53 43 4e 54 52 47 30 3b 03"   # 停止读码
//
// 常用 UDI 指令（ASCII -> HEX）：
//   开始读码(触发一次) : ~<SOH>0000#SCNTRG1;<ETX>
//                         7e 01 30 30 30 30 23 53 43 4e 54 52 47 31 3b 03
//   停止读码            : ~<SOH>0000#SCNTRG0;<ETX>
//                         7e 01 30 30 30 30 23 53 43 4e 54 52 47 30 3b 03
//   查询系统信息        : ~<SOH>0000@QRYSYS;<ETX>
//                         7e 01 30 30 30 30 40 51 52 59 53 59 53 3b 03
#include "libnldevicemaster.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

int main(int argc, char **argv) {
    int cnt = 0;
    HANDLEDEVLST lst = nl_EnumDevices(&cnt);
    printf("[NLS] device count = %d\n", cnt);
    if (!lst || cnt <= 0) {
        printf("[NLS] 未发现设备\n");
        return 1;
    }

    const char *hex = (argc > 1) ? argv[1]
        : "7e 01 30 30 30 30 23 53 43 4e 54 52 47 31 3b 03"; // SCNTRG1 开始读码
    printf("[NLS] 发送指令 HEX: %s\n", hex);
    int scan_mode = (argc > 1 && strcmp(argv[1], "--scan") == 0) ? 1 : 0;

    for (int i = 0; i < cnt; i++) {
        HANDLEDEV dev = nl_OpenDevice(lst, (unsigned)i, Nlscan);
        printf("[NLS] device[%d] handle=%p\n", i, (void*)dev);
        if (!dev)
            continue;

        if (scan_mode) {
            printf("[NLS] 触发扫描并抓取条码 %d 秒...\n", 6);
            int r = (int)nl_SendCommand(dev, "SCNTRG1", 7);
            printf("[NLS] SendCommand[SCNTRG1] -> %d (1=支持)\n", r);
            // 触发后持续读取扫码枪返回的条码数据
            time_t t0 = time(NULL);
            while (time(NULL) - t0 < 6) {
                char buf[512] = {0};
                unsigned int n = nl_Read(dev, buf, sizeof(buf) - 1, 500);
                if (n > 0) {
                    printf("[NLS] RX(%u): %.256s\n", n, buf);
                    for (unsigned k = 0; k < n; k++)
                        printf("%02x ", (unsigned char)buf[k]);
                    printf("\n");
                }
            }
            nl_CloseDevice(&dev);
            break;
        }

        // 尝试多种指令写法（SDK 内部会打包 UDI 帧，因此传“纯指令”文本）
        const char *cands[] = {
            "QRYSYS",       // 查询系统信息
            "SCNTRG1",      // 开始读码（FD2 需模式支持）
            "#SCNTRG1",
            "SCNTRG0",
            "#SCNTRG0",
        };
        for (size_t k = 0; k < sizeof(cands) / sizeof(cands[0]); k++) {
            printf("-- SendCommand[%s] -> %d (1=支持 2=不支持 0=错误)\n",
                   cands[k], (int)nl_SendCommand(dev, cands[k], (unsigned)strlen(cands[k])));
        }
        // 以纯指令 HEX 方式发送（不带 7E 前缀/3B 03 后缀）
        const char *hexcands[] = {
            "51 52 59 53 59 53",        // QRYSYS
            "53 43 4e 54 52 47 31",     // SCNTRG1
            "23 53 43 4e 54 52 47 31",  // #SCNTRG1
        };
        for (size_t k = 0; k < sizeof(hexcands) / sizeof(hexcands[0]); k++) {
            printf("-- SendCommandAsHex[%s] -> %d\n",
                   hexcands[k], (int)nl_SendCommandAsHex(dev, hexcands[k], (unsigned)strlen(hexcands[k])));
        }
        char buf[2048] = {0};
        unsigned int n = nl_Read(dev, buf, sizeof(buf) - 1, 1500);
        printf("[NLS] read n=%u data=%.*s\n", n, (int)(n > 200 ? 200 : n), buf);
        nl_CloseDevice(&dev);
        break; // 只操作第一个扫码设备
    }
    nl_ReleaseDevices(&lst);
    printf("[NLS] done\n");
    return 0;
}