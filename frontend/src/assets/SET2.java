import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n =sc.nextInt();
        int[] arr = new int[n];
        for(int i=0;i<n;i++){
            arr[i]=sc.nextInt();
        }
        Set<Integer> set = new LinkedHashSet<>();
        Set<Integer> seen = new HashSet<>();
        Set<Integer> duplicate = new LinkedHashSet<>();
        for(int x : arr){
            set.add(x);
        }
        
        for(int x : set){
            System.out.print(x+" "+"\n");
        }
        
        for(int x : arr){
            if(!seen.add(x)){
                duplicate.add(x);    
            }
        }
        for(int x:duplicate){
            System.out.print(x+" ");
        }
    }
}