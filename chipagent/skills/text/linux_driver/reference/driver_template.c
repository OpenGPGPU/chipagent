/*
 * {{module_name}}_driver.c — Linux platform driver for {{module_name}}_reg_top
 *
 * Maps the register-block MMIO region and exposes CTRL/STATUS helpers via
 * the {{module_name}}_regs.h defines. Skeleton only.
 */

#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/io.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/of.h>
#include "{{module_name}}_regs.h"

struct {{module_name}}_ctx {
    void __iomem *base;
    struct miscdevice misc;
};

static u32 {{module_name}}_read_reg(struct {{module_name}}_ctx *ctx, u32 off)
{
    return ioread32(ctx->base + off);
}

static void {{module_name}}_write_reg(struct {{module_name}}_ctx *ctx, u32 off, u32 val)
{
    iowrite32(val, ctx->base + off);
}

static ssize_t {{module_name}}_write(struct file *f, const char __user *buf,
                                     size_t n, loff_t *off)
{
    struct {{module_name}}_ctx *ctx = container_of(f->private_data,
                                                   struct {{module_name}}_ctx, misc);
    /* Minimal: write 4 bytes -> CTRL register */
    u32 val = 0;
    if (n < sizeof(u32))
        return -EINVAL;
    if (copy_from_user(&val, buf, sizeof(u32)))
        return -EFAULT;
    {{module_name}}_write_reg(ctx, CTRL_ADDR, val);
    return sizeof(u32);
}

static ssize_t {{module_name}}_read(struct file *f, char __user *buf,
                                    size_t n, loff_t *off)
{
    struct {{module_name}}_ctx *ctx = container_of(f->private_data,
                                                   struct {{module_name}}_ctx, misc);
    u32 val = {{module_name}}_read_reg(ctx, STATUS_ADDR);
    if (n < sizeof(u32))
        return -EINVAL;
    if (copy_to_user(buf, &val, sizeof(u32)))
        return -EFAULT;
    return sizeof(u32);
}

static const struct file_operations {{module_name}}_fops = {
    .owner = THIS_MODULE,
    .write = {{module_name}}_write,
    .read  = {{module_name}}_read,
};

static int {{module_name}}_probe(struct platform_device *pdev)
{
    struct {{module_name}}_ctx *ctx;
    struct resource *res;

    ctx = devm_kzalloc(&pdev->dev, sizeof(*ctx), GFP_KERNEL);
    if (!ctx)
        return -ENOMEM;

    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    ctx->base = devm_ioremap_resource(&pdev->dev, res);
    if (IS_ERR(ctx->base))
        return PTR_ERR(ctx->base);

    ctx->misc.minor = MISC_DYNAMIC_MINOR;
    ctx->misc.name = "{{module_name}}";
    ctx->misc.fops = &{{module_name}}_fops;

    /* Kick the block: assert CTRL.start */
    {{module_name}}_write_reg(ctx, CTRL_ADDR, CTRL_START_MASK);

    return misc_register(&ctx->misc);
}

static int {{module_name}}_remove(struct platform_device *pdev)
{
    struct {{module_name}}_ctx *ctx = platform_get_drvdata(pdev);
    misc_deregister(&ctx->misc);
    return 0;
}

static const struct of_device_id {{module_name}}_match[] = {
    { .compatible = "{{module_name}},reg-top", },
    { /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, {{module_name}}_match);

static struct platform_driver {{module_name}}_driver = {
    .probe  = {{module_name}}_probe,
    .remove = {{module_name}}_remove,
    .driver = {
        .name = "{{module_name}}_reg_top",
        .of_match_table = {{module_name}}_match,
    },
};
module_platform_driver({{module_name}}_driver);

MODULE_AUTHOR("ChipAgent");
MODULE_DESCRIPTION("{{module_name}} register block driver");
MODULE_LICENSE("GPL");
