import ConfigProvider from '@/components/ui/ConfigProvider'
import { themeConfig } from '@/configs/theme.config'
import useDarkMode from '@/utils/hooks/useDarkMode'
import useThemeSchema from '@/utils/hooks/useThemeSchema'
import useLocale from '@/utils/hooks/useLocale'
import useDirection from '@/utils/hooks/useDirection'
import { MODE_DARK, MODE_LIGHT } from '@/constants/theme.constant'
import type { CommonProps } from '@/@types/common'

const Theme = (props: CommonProps) => {
    useThemeSchema()
    const [isDarkMode] = useDarkMode()
    const [direction] = useDirection()

    const { locale } = useLocale()

    return (
        <ConfigProvider
            value={{
                locale: locale,
                ...themeConfig,
                direction,
                mode: isDarkMode ? MODE_DARK : MODE_LIGHT,
            }}
        >
            {props.children}
        </ConfigProvider>
    )
}

export default Theme
