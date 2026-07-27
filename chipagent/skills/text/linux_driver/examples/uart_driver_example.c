/* uart_driver.c — Linux platform driver for uart_reg_top (style reference) */

#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include "uart_regs.h"

struct uart_ctx { void __iomem *base; struct miscdevice misc; };

static u32 uart_read_reg(struct uart_ctx *c, u32 off) { return ioread32(c->base + off); }
static void uart_write_reg(struct uart_ctx *c, u32 off, u32 v) { iowrite32(v, c->base + off); }

static ssize_t uart_write(struct file *f, const char __user *buf, size_t n, loff_t *off)
{
    struct uart_ctx *c = container_of(f->private_data, struct uart_ctx, misc);
    u32 v = 0;
    if (n < 4) return -EINVAL;
    if (copy_from_user(&v, buf, 4)) return -EFAULT;
    uart_write_reg(c, CTRL_ADDR, v);
    return 4;
}

static ssize_t uart_read(struct file *f, char __user *buf, size_t n, loff_t *off)
{
    struct uart_ctx *c = container_of(f->private_data, struct uart_ctx, misc);
    u32 v = uart_read_reg(c, STATUS_ADDR);
    if (n < 4) return -EINVAL;
    if (copy_to_user(buf, &v, 4)) return -EFAULT;
    return 4;
}

static const struct file_operations uart_fops = { .owner = THIS_MODULE, .write = uart_write, .read = uart_read };

static int uart_probe(struct platform_device *pdev)
{
    struct uart_ctx *c;
    struct resource *res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    c = devm_kzalloc(&pdev->dev, sizeof(*c), GFP_KERNEL);
    if (!c) return -ENOMEM;
    c->base = devm_ioremap_resource(&pdev->dev, res);
    if (IS_ERR(c->base)) return PTR_ERR(c->base);
    c->misc.minor = MISC_DYNAMIC_MINOR; c->misc.name = "uart"; c->misc.fops = &uart_fops;
    uart_write_reg(c, CTRL_ADDR, CTRL_START_MASK);
    return misc_register(&c->misc);
}

static int uart_remove(struct platform_device *pdev)
{
    struct uart_ctx *c = platform_get_drvdata(pdev);
    misc_deregister(&c->misc);
    return 0;
}

static const struct of_device_id uart_match[] = { { .compatible = "uart,reg-top", }, { /* sentinel */ } };
MODULE_DEVICE_TABLE(of, uart_match);
static struct platform_driver uart_driver = {
    .probe = uart_probe, .remove = uart_remove,
    .driver = { .name = "uart_reg_top", .of_match_table = uart_match, },
};
module_platform_driver(uart_driver);
MODULE_AUTHOR("ChipAgent"); MODULE_DESCRIPTION("uart register block driver"); MODULE_LICENSE("GPL");
