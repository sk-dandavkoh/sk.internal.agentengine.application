import ChatSideNav from './components/ChatSideNav'
import ChatView from './components/ChatView'
import ChatHistoryRenameDialog from './components/ChatHistoryRenameDialog'
import useResponsive from '@/utils/hooks/useResponsive'
import { usGenerativeChatStore } from './store/generativeChatStore'
import { apiGetChatHistory } from '@/services/AiService'
import useSWR from 'swr'
import type { GetChatHistoryResponse } from './types'

const GenerativeChat = () => {
    const { larger } = useResponsive()

    const { setChatHistory } = usGenerativeChatStore()

    useSWR(
        ['/api/ai/chat/history'],
        () => apiGetChatHistory<GetChatHistoryResponse>(),
        {
            revalidateOnFocus: false,
            revalidateIfStale: false,
            revalidateOnReconnect: false,
            onSuccess: (data) => {
                console.log('adta', data)
                setChatHistory(data)
            },
        },
    )

    return (
        <div className="h-full">
            <div className="flex flex-auto gap-4 h-full">
                <ChatView />
                {larger.xl && <ChatSideNav />}
                <ChatHistoryRenameDialog />
            </div>
        </div>
    )
}

export default GenerativeChat
